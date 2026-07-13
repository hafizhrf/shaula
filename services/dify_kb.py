"""
Dify knowledge-base (RAG) client for Emilia.

Dify 1.14.2 runs locally on this VPS; its dataset Service API gives Emilia semantic
recall over SOPs / runbooks / lessons that the small Hermes model can't hold in-prompt.
We only use the dataset endpoints (retrieve + ingest) — Hermes stays the router/persona.

Embeddings are local & free (Ollama `nomic-embed-text` configured as a Dify provider).
Everything is best-effort: any error returns empty/False and is logged, never crashing the
message handler. Gated by config.DIFY_KB_ENABLED (blank dataset id/key = feature off).
"""
import json
import logging
import urllib.request
import urllib.error

import config

logger = logging.getLogger(__name__)

_TIMEOUT = 20


def _post(path: str, payload: dict, timeout: int = _TIMEOUT) -> dict | None:
    """POST JSON to the Dify Service API with the dataset bearer key. None on any error."""
    url = f"{config.DIFY_BASE_URL.rstrip('/')}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {config.DIFY_DATASET_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        logger.warning("Dify KB %s HTTP %s: %s", path, e.code, body)
    except Exception as e:
        logger.warning("Dify KB %s failed: %s", path, e)
    return None


def retrieve(query: str, top_k: int = 4) -> list[dict]:
    """Semantic search the knowledge base. Returns [{content, score}], or [] on error/disabled."""
    if not config.DIFY_KB_ENABLED or not query.strip():
        return []
    result = _post(
        f"/v1/datasets/{config.DIFY_DATASET_ID}/retrieve",
        {
            "query": query,
            "retrieval_model": {
                "search_method": "semantic_search",
                "reranking_enable": False,
                "top_k": top_k,
                "score_threshold_enabled": False,
            },
        },
    )
    if not result:
        return []
    chunks = []
    for rec in result.get("records", []):
        content = (rec.get("segment") or {}).get("content", "").strip()
        if content:
            chunks.append({"content": content, "score": rec.get("score", 0)})
    return chunks


def ingest(title: str, text: str) -> bool:
    """Add a text document to the knowledge base (used by save_correction). False on error/disabled."""
    if not config.DIFY_KB_ENABLED or not text.strip():
        return False
    result = _post(
        f"/v1/datasets/{config.DIFY_DATASET_ID}/document/create-by-text",
        {
            "name": title[:60] or "note",
            "text": text,
            "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"},
        },
        timeout=60,  # ingestion triggers embedding — give it room
    )
    return result is not None


def format_chunks(chunks: list[dict]) -> str:
    """Compact bullet list of retrieved chunks for injecting into a Hermes prompt."""
    return "\n\n".join(f"- {c['content']}" for c in chunks)
