"""
What's deployed on this VPS: nginx subdomains (with origin port + up/down) and the app
dirs in the workspace. Backs Emilia's `deployments` tool — read-only, no deps.
"""
import asyncio
import glob
import os
import re

NGINX_SITES = "/etc/nginx/sites-enabled"
WORKSPACE = "/home/ubuntu/workspace"
_TOOLCHAIN_DIRS = {"flutter", "android-sdk"}  # big vendored toolchains, not "apps"

_SERVER_NAME_RE = re.compile(r'^\s*server_name\s+([^;]+);', re.M)
_PROXY_RE = re.compile(r'^\s*proxy_pass\s+https?://127\.0\.0\.1:(\d+)', re.M)
_SSL_RE = re.compile(r'^\s*listen\s+.*\b443\b.*ssl', re.M)


def list_sites():
    """Parse nginx vhosts → [{fqdn, port, ssl}]. Skips the default '_' server + comments."""
    sites = []
    for path in sorted(glob.glob(os.path.join(NGINX_SITES, "*"))):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                # Drop commented lines so '# server_name example.com;' is ignored.
                text = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
        except OSError:
            continue
        fqdns = [n for m in _SERVER_NAME_RE.finditer(text)
                 for n in m.group(1).split() if n and n != "_"]
        ports = [int(p) for p in _PROXY_RE.findall(text)]
        ssl = bool(_SSL_RE.search(text))
        port = ports[0] if ports else None
        for fqdn in dict.fromkeys(fqdns):  # dedupe, preserve order
            sites.append({"fqdn": fqdn, "port": port, "ssl": ssl})
    return sites


async def _port_up(port, timeout=2.0):
    """True if something is listening on 127.0.0.1:<port>, False if not, None if no port."""
    if not port:
        return None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def sites_with_status():
    """list_sites() + each site's origin up/down probed concurrently."""
    sites = list_sites()
    if not sites:
        return []

    async def _annot(s):
        s = dict(s)
        s["up"] = await _port_up(s["port"])
        return s

    return list(await asyncio.gather(*[_annot(s) for s in sites]))


def list_workspace_apps():
    """App dirs in the workspace (excludes hidden dirs + big vendored toolchains)."""
    apps = []
    try:
        for name in sorted(os.listdir(WORKSPACE)):
            full = os.path.join(WORKSPACE, name)
            if name.startswith(".") or not os.path.isdir(full) or name in _TOOLCHAIN_DIRS:
                continue
            apps.append(name)
    except OSError:
        pass
    return apps
