#!/usr/bin/env python3
"""
Lightweight run-history backend and SPA static file server.
Serves the React + Vite + Tailwind frontend and provides high-speed JSON REST APIs.
"""
import glob
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_DBS = [
    os.path.join(BASE_DIR, "../data/runs.db"),
    "/home/ubuntu/workspace/shaula/data/runs.db",
    "/home/ubuntu/workspace/discord-devops-bot/data/runs.db",
]
DB_PATH        = next((p for p in _CANDIDATE_DBS if os.path.exists(p)), _CANDIDATE_DBS[0])
ENV_PATH       = os.path.join(BASE_DIR, ".env")
DIST_DIR       = os.path.join(BASE_DIR, "dist")
HOST           = "127.0.0.1"
PORT           = 5005
PAGE_LIMIT     = 25


# ── Load .env ──────────────────────────────────────────────────────────────
def _load_env() -> dict:
    env = {}
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env

_ENV           = _load_env()
AUTH_PASSWORD  = _ENV.get("AUTH_PASSWORD", "")
_SESSION_KEY   = hashlib.sha256(f"rh:{AUTH_PASSWORD}".encode()).hexdigest()
SESSION_COOKIE = "rh_sess"
SESSION_TTL    = 86400 * 7   # 7 days


# ── Session token (HMAC-signed timestamp, no server-side storage) ──────────
def _make_token() -> str:
    ts  = str(int(time.time()))
    sig = hmac.new(_SESSION_KEY.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _verify_token(token: str) -> bool:
    try:
        ts, sig = token.rsplit(".", 1)
        expected = hmac.new(_SESSION_KEY.encode(), ts.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return (int(time.time()) - int(ts)) <= SESSION_TTL
    except Exception:
        return False


def _parse_cookies(headers) -> dict:
    cookies = {}
    for part in headers.get("Cookie", "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def _is_authed(handler) -> bool:
    token = _parse_cookies(handler.headers).get(SESSION_COOKIE, "")
    return bool(token) and _verify_token(token)


# ── DB Helpers ─────────────────────────────────────────────────────────────
def query_db(state_filter: str, search: str, offset: int, limit: int = PAGE_LIMIT) -> tuple[list[dict], int]:
    try:
        con = sqlite3.connect(DB_PATH, timeout=5)
        con.row_factory = sqlite3.Row
        where_clauses, params = [], []
        if state_filter:
            where_clauses.append("state = ?")
            params.append(state_filter)
        if search:
            like = f"%{search}%"
            where_clauses.append("(description LIKE ? OR task_id LIKE ? OR session_id LIKE ? OR account LIKE ?)")
            params.extend([like, like, like, like])
        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        total = con.execute(f"SELECT COUNT(*) FROM runs {where}", params).fetchone()[0]
        rows  = con.execute(
            f"SELECT * FROM runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        con.close()
        res = []
        for r in rows:
            d = dict(r)
            d["prompt_tokens"] = int(d.get("prompt_tokens") or 0)
            d["completion_tokens"] = int(d.get("completion_tokens") or 0)
            d["total_tokens"] = int(d.get("total_tokens") or 0)
            d["cost_usd"] = float(d.get("cost_usd") or 0.0)
            res.append(d)
        return res, total
    except Exception:
        return [], 0


def get_stats() -> dict:
    try:
        con  = sqlite3.connect(DB_PATH, timeout=5)
        rows = con.execute("SELECT state, COUNT(*) FROM runs GROUP BY state").fetchall()
        stats = {r[0]: r[1] for r in rows}
        agg = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_usd), 0.0) FROM runs"
        ).fetchone()
        stats["total_runs"] = agg[0] or 0
        stats["total_tokens_sum"] = int(agg[1] or 0)
        stats["total_cost_sum"] = round(float(agg[2] or 0.0), 4)
        con.close()
        return stats
    except Exception:
        return {"total_runs": 0, "total_tokens_sum": 0, "total_cost_sum": 0.0}


def get_run_by_id(task_id: str) -> dict | None:
    try:
        con = sqlite3.connect(DB_PATH, timeout=5)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM runs WHERE task_id = ? LIMIT 1", (task_id,)).fetchone()
        con.close()
        if not row:
            return None
        d = dict(row)
        d["prompt_tokens"] = int(d.get("prompt_tokens") or 0)
        d["completion_tokens"] = int(d.get("completion_tokens") or 0)
        d["total_tokens"] = int(d.get("total_tokens") or 0)
        d["cost_usd"] = float(d.get("cost_usd") or 0.0)
        return d
    except Exception:
        return None


# ── Cron Readers ────────────────────────────────────────────────────────────
EMILIA_CRON_PATHS = [
    "/opt/agent/projects/.claude/scheduled_tasks.json",
    "/opt/agent/projects/session-1518326535675842681/.claude/scheduled_tasks.json",
    os.path.join(BASE_DIR, "../.claude/scheduled_tasks.json"),
    "/home/ubuntu/workspace/shaula/.claude/scheduled_tasks.json",
    "/home/ubuntu/workspace/discord-devops-bot/.claude/scheduled_tasks.json",
    "/home/ubuntu/.claude/scheduled_tasks.json",
]


def _split_cron(line: str, has_user: bool):
    """Return (schedule, command) for a cron line, or None if not a job line."""
    line = line.strip()
    if line.startswith("@"):
        n = 2 if has_user else 1
        parts = line.split(None, n)
        if len(parts) <= n:
            return None
        return parts[0], parts[-1]
    n = 6 if has_user else 5
    parts = line.split(None, n)
    if has_user:
        if len(parts) < 7:
            return None
        return " ".join(parts[:5]), parts[6]
    if len(parts) < 6:
        return None
    return " ".join(parts[:5]), parts[5]


def _read_cron_text(text: str, source: str, has_user: bool) -> list[dict]:
    jobs, pending_comment = [], ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            pending_comment = ""
            continue
        if s.startswith("#"):
            pending_comment = s.lstrip("#").strip()
            continue
        first = s.split(None, 1)[0]
        if "=" in first and not first[0].isdigit() and not first.startswith("@"):
            continue
        parsed = _split_cron(s, has_user)
        if parsed:
            jobs.append({
                "schedule": parsed[0],
                "command":  parsed[1],
                "source":   source,
                "comment":  pending_comment,
            })
        pending_comment = ""
    return jobs


def get_system_crons() -> list[dict]:
    jobs = []
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            jobs += _read_cron_text(out.stdout, "crontab · ubuntu", has_user=False)
    except Exception:
        pass
    try:
        with open("/etc/crontab", "r", encoding="utf-8", errors="replace") as f:
            jobs += _read_cron_text(f.read(), "/etc/crontab", has_user=True)
    except Exception:
        pass
    for p in sorted(glob.glob("/etc/cron.d/*")):
        name = os.path.basename(p)
        if name.startswith("."):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                jobs += _read_cron_text(f.read(), f"cron.d/{name}", has_user=True)
        except Exception:
            pass
    return jobs


def get_emilia_crons() -> list[dict]:
    jobs, seen = [], set()
    for path in EMILIA_CRON_PATHS:
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict):
            entries = data.get("tasks") or data.get("jobs") or data.get("scheduled_tasks") or []
            if isinstance(entries, dict):
                entries = list(entries.values())
        elif isinstance(data, list):
            entries = data
        else:
            entries = []
        for it in entries:
            if not isinstance(it, dict):
                continue
            jobs.append({
                "cron":      it.get("cron") or it.get("schedule") or "",
                "prompt":    it.get("prompt") or it.get("description") or it.get("task") or "",
                "recurring": it.get("recurring", True),
                "id":        str(it.get("id") or it.get("job_id") or ""),
                "source":    path,
            })
    return jobs


# ── Cron → human-readable schedule (Indonesian) ─────────────────────────────
_DOW_ID = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"]
_MON_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
           "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def _dow_names(dow: str) -> str:
    def nm(x):
        try:
            return _DOW_ID[int(x) % 7]
        except Exception:
            return None
    if "/" in dow:
        return dow
    if "-" in dow and "," not in dow:
        a, b = dow.split("-", 1)
        na, nb = nm(a), nm(b)
        return f"{na}–{nb}" if na and nb else dow
    if "," in dow:
        names = [nm(x) for x in dow.split(",")]
        return ", ".join(names) if all(names) else dow
    return nm(dow) or dow


def _day_scope(dom: str, mon: str, dow: str) -> str:
    if dom == "*" and mon == "*" and dow == "*":
        return "tiap hari"
    bits = []
    if dow != "*":
        bits.append(_dow_names(dow))
    if dom != "*":
        bits.append(f"tiap {dom[2:]} hari" if dom.startswith("*/") else f"tgl {dom}")
    if mon != "*":
        try:
            bits.append(_MON_ID[int(mon)])
        except Exception:
            bits.append(f"bln {mon}")
    return ", ".join(b for b in bits if b) or "tiap hari"


def cron_human(expr: str) -> str:
    """Turn a cron expression into a short Indonesian description (best-effort)."""
    expr = (expr or "").strip()
    aliases = {
        "@reboot": "Saat boot", "@yearly": "Tiap tahun", "@annually": "Tiap tahun",
        "@monthly": "Tiap bulan", "@weekly": "Tiap Minggu", "@daily": "Tiap hari 00:00",
        "@midnight": "Tiap hari 00:00", "@hourly": "Tiap jam",
    }
    if expr in aliases:
        return aliases[expr]
    p = expr.split()
    if len(p) != 5:
        return ""
    mi, ho, dom, mon, dow = p
    scope = _day_scope(dom, mon, dow)

    def cap(s): return s[0].upper() + s[1:] if s else s
    def with_scope(base): return cap(base) if scope == "tiap hari" else f"{cap(scope)}, {base}"

    if mi.startswith("*/") and ho == "*":
        return with_scope(f"tiap {mi[2:]} menit")
    if ho.startswith("*/"):
        extra = f" menit {mi}" if mi not in ("0", "*") else ""
        return with_scope(f"tiap {ho[2:]} jam{extra}")
    if ho == "*":
        return with_scope("tiap menit" if mi == "*" else f"tiap jam menit {mi}")
    if ho.isdigit() and mi.isdigit():
        t = f"{int(ho):02d}:{int(mi):02d}"
    else:
        t = f"{ho}:{mi}"
    return f"Tiap hari, {t}" if scope == "tiap hari" else f"{cap(scope)}, {t}"


def get_crons_api() -> dict:
    sys_jobs = get_system_crons()
    for j in sys_jobs:
        j["human_schedule"] = cron_human(j.get("schedule", ""))
    em_jobs = get_emilia_crons()
    for j in em_jobs:
        j["human_schedule"] = cron_human(j.get("cron", ""))
    return {
        "system": sys_jobs,
        "emilia": em_jobs,
    }


# ── Transcript Loader ──────────────────────────────────────────────────────
def load_session_transcript(session_id: str) -> dict:
    if not session_id:
        return {"found": False, "session_id": "", "steps": []}

    # 1. Antigravity Brain Logs
    brain_dir = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{session_id}/.system_generated/logs")
    full_path = os.path.join(brain_dir, "transcript_full.jsonl")
    compact_path = os.path.join(brain_dir, "transcript.jsonl")
    target = full_path if os.path.isfile(full_path) else (compact_path if os.path.isfile(compact_path) else None)

    if target:
        steps = []
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    stype = item.get("type")
                    if stype == "USER_INPUT":
                        raw = item.get("content", "") or ""
                        req_m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", raw, re.DOTALL)
                        txt = req_m.group(1).strip() if req_m else raw.strip()
                        txt = re.sub(r"\[System Instruction:.*?\]", "", txt, flags=re.DOTALL).strip()
                        steps.append({
                            "type": "user",
                            "text": txt or raw.strip(),
                            "timestamp": item.get("created_at")
                        })
                    elif stype == "PLANNER_RESPONSE":
                        if item.get("thinking"):
                            steps.append({
                                "type": "thinking",
                                "text": item["thinking"].strip()
                            })
                        if item.get("tool_calls"):
                            for tc in item["tool_calls"]:
                                steps.append({
                                    "type": "tool_call",
                                    "name": tc.get("name", "tool"),
                                    "args": tc.get("args", {}),
                                    "output": ""
                                })
                        if item.get("content") and item["content"].strip():
                            steps.append({
                                "type": "assistant",
                                "text": item["content"].strip(),
                                "timestamp": item.get("created_at")
                            })
                    elif stype == "GENERIC":
                        out = item.get("content", "") or ""
                        for s in reversed(steps):
                            if s["type"] == "tool_call" and not s["output"]:
                                s["output"] = out
                                break
            return {"found": True, "session_id": session_id, "engine": "agy", "steps": steps}
        except Exception as e:
            return {"found": False, "session_id": session_id, "error": str(e), "steps": []}

    # 2. Claude Logs
    claude_patterns = [
        os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl"),
        f"/opt/agent/projects/*/{session_id}.jsonl",
        os.path.expanduser(f"~/.claude/sessions/{session_id}.jsonl"),
    ]
    claude_target = None
    for pat in claude_patterns:
        m = glob.glob(pat)
        if m:
            claude_target = m[0]
            break

    if claude_target:
        steps = []
        tool_call_map = {}
        try:
            with open(claude_target, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    itype = item.get("type")
                    msg = item.get("message", {})
                    if itype == "user" and isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = []
                            for b in content:
                                if isinstance(b, dict):
                                    if b.get("type") == "text":
                                        text_parts.append(b.get("text", ""))
                                    elif b.get("type") == "tool_result":
                                        tid = b.get("tool_use_id")
                                        res_content = b.get("content", "")
                                        if isinstance(res_content, list):
                                            res_content = "\n".join(x.get("text", "") for x in res_content if isinstance(x, dict))
                                        if tid in tool_call_map:
                                            tool_call_map[tid]["output"] = str(res_content)
                            if text_parts:
                                steps.append({
                                    "type": "user",
                                    "text": "".join(text_parts).strip(),
                                    "timestamp": item.get("timestamp")
                                })
                        elif isinstance(content, str) and content.strip():
                            steps.append({
                                "type": "user",
                                "text": content.strip(),
                                "timestamp": item.get("timestamp")
                            })
                    elif itype == "assistant" and isinstance(msg, dict):
                        content = msg.get("content", [])
                        if isinstance(content, str):
                            content = [{"type": "text", "text": content}]
                        for b in content:
                            if not isinstance(b, dict):
                                continue
                            btype = b.get("type")
                            if btype == "thinking":
                                th = b.get("thinking", "")
                                if th.strip():
                                    steps.append({"type": "thinking", "text": th.strip()})
                            elif btype == "tool_use":
                                tid = b.get("id", "")
                                tc_step = {
                                    "type": "tool_call",
                                    "name": b.get("name", "tool"),
                                    "args": b.get("input", {}),
                                    "output": ""
                                }
                                steps.append(tc_step)
                                if tid:
                                    tool_call_map[tid] = tc_step
                            elif btype == "text":
                                txt = b.get("text", "")
                                if txt.strip():
                                    steps.append({
                                        "type": "assistant",
                                        "text": txt.strip(),
                                        "timestamp": item.get("timestamp")
                                    })
            return {"found": True, "session_id": session_id, "engine": "claude", "steps": steps}
        except Exception as e:
            return {"found": False, "session_id": session_id, "error": str(e), "steps": []}

    return {"found": False, "session_id": session_id, "steps": []}


# ── MIME Types & Static Serving ────────────────────────────────────────────
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".mjs":  "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf":  "font/ttf",
}


# ── HTTP Handler ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, content_type: str = "text/html; charset=utf-8", extra_headers: list = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, code: int = 200, extra_headers: list = None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, content_type="application/json; charset=utf-8", extra_headers=extra_headers)

    def _redirect(self, location: str, extra_headers: list = None):
        self.send_response(303)
        self.send_header("Location", location)
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()

    def _serve_file(self, file_path: str):
        if not os.path.isfile(file_path):
            self._send(404, b"Not found", content_type="text/plain; charset=utf-8")
            return
        ext = os.path.splitext(file_path)[1].lower()
        ctype = MIME_TYPES.get(ext, mimetypes.guess_type(file_path)[0] or "application/octet-stream")
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self._send(200, content, content_type=ctype)
        except Exception as e:
            self._send(500, f"Error reading file: {e}".encode(), content_type="text/plain; charset=utf-8")

    def _serve_spa(self):
        index_file = os.path.join(DIST_DIR, "index.html")
        if os.path.isfile(index_file):
            self._serve_file(index_file)
        else:
            self._send(
                500,
                b"Frontend build not found. Please run 'npm run build' inside frontend/ directory.",
                content_type="text/plain; charset=utf-8",
            )

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        # Logout route
        if path == "/logout":
            self._redirect("/", extra_headers=[
                ("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict")
            ])
            return

        # API: /api/me
        if path == "/api/me":
            self._send_json({"authenticated": _is_authed(self)})
            return

        # API: /api/stats
        if path == "/api/stats":
            if not _is_authed(self):
                self._send_json({"error": "Unauthorized"}, code=401)
                return
            self._send_json(get_stats())
            return

        # API: /api/runs
        if path == "/api/runs":
            if not _is_authed(self):
                self._send_json({"error": "Unauthorized"}, code=401)
                return
            qs     = urllib.parse.parse_qs(parsed.query)
            state  = qs.get("state", [""])[0].strip()
            search = qs.get("q", [""])[0].strip()
            try:
                offset = max(0, int(qs.get("offset", [0])[0]))
            except ValueError:
                offset = 0
            try:
                limit = min(100, max(1, int(qs.get("limit", [PAGE_LIMIT])[0])))
            except ValueError:
                limit = PAGE_LIMIT
            rows, total = query_db(state, search, offset, limit)
            self._send_json({"runs": rows, "total": total, "offset": offset, "limit": limit})
            return

        # API: /api/runs/<task_id>/transcript or /api/runs/<task_id>
        if path.startswith("/api/runs/"):
            if not _is_authed(self):
                self._send_json({"error": "Unauthorized"}, code=401)
                return
            subpath = path[len("/api/runs/"):].strip("/")
            if "/transcript" in subpath:
                task_id = subpath.split("/transcript")[0].strip("/")
                run = get_run_by_id(task_id)
                session_id = run.get("session_id") if run else task_id
                t = load_session_transcript(session_id)
                t["task_id"] = task_id
                if run and run.get("session_id"):
                    t["session_id"] = run["session_id"]
                self._send_json(t)
            else:
                task_id = subpath
                run = get_run_by_id(task_id)
                if not run:
                    self._send_json({"error": f"Run '{task_id}' not found"}, code=404)
                else:
                    self._send_json(run)
            return

        # API: /api/crons
        if path == "/api/crons":
            if not _is_authed(self):
                self._send_json({"error": "Unauthorized"}, code=401)
                return
            self._send_json(get_crons_api())
            return

        # Static assets (Vite dist bundle)
        clean_path = path.lstrip("/")
        if clean_path:
            candidate = os.path.abspath(os.path.join(DIST_DIR, clean_path))
            if candidate.startswith(DIST_DIR) and os.path.isfile(candidate):
                self._serve_file(candidate)
                return

        # Client-side SPA routing fallback (/ , /run/:taskId, /cron, etc.)
        self._serve_spa()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/api/login", "/login"):
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b""
            content_type = self.headers.get("Content-Type", "")
            password = ""
            if "application/json" in content_type:
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    password = payload.get("password", "")
                except Exception:
                    password = ""
            else:
                params = urllib.parse.parse_qs(raw_body.decode("utf-8", errors="replace"))
                password = params.get("password", [""])[0]

            is_api = (path == "/api/login" or "application/json" in self.headers.get("Accept", ""))

            if password and password == AUTH_PASSWORD:
                token = _make_token()
                cookie = f"{SESSION_COOKIE}={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Strict"
                if is_api:
                    self._send_json({"ok": True}, extra_headers=[("Set-Cookie", cookie)])
                else:
                    self._redirect("/", extra_headers=[("Set-Cookie", cookie)])
            else:
                if is_api:
                    self._send_json({"ok": False, "error": "Password salah. Coba lagi ya Shisou~ (っ*´∀｀*)っ"}, code=401)
                else:
                    self._redirect("/?error=1")
            return

        if path in ("/api/logout", "/logout"):
            cookie = f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"
            if path == "/api/logout" or "application/json" in self.headers.get("Accept", ""):
                self._send_json({"ok": True}, extra_headers=[("Set-Cookie", cookie)])
            else:
                self._redirect("/", extra_headers=[("Set-Cookie", cookie)])
            return

        self._send_json({"error": "Not found"}, code=404)


if __name__ == "__main__":
    srv = HTTPServer((HOST, PORT), Handler)
    print(f"runs-history listening on {HOST}:{PORT}")
    srv.serve_forever()
