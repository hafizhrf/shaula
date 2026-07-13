"""
Continual memory capture — how Emilia "learns" over time.

After a chat exchange, a lightweight local-Hermes pass decides whether Apis revealed a
DURABLE fact / preference / lesson worth remembering, and if so ingests it into the Dify
knowledge base (deduped via vector similarity). This is retrieval memory (RAG), NOT model
training — Hermes's weights don't change; Emilia just accumulates recallable knowledge.

Memory-aware by design:
- Runs in the BACKGROUND (asyncio task) so it never delays the user's reply.
- Piggybacks on the Hermes model already warm from the reply; Ollama serializes requests,
  so no second model copy is loaded (no extra always-on RAM).
- No-op when the KB is disabled or Dify is down (ingest fails gracefully).
- Selective + deduped: only durable facts are stored, and near-identical ones are skipped,
  keeping retrieval quality high (the opposite of dumping every message in).
"""
import logging
import re

import config
from services import conversation, dify_kb

# Strip a leaked leading label the 3B model sometimes echoes (e.g. "Jawaban:", "Fakta:",
# "Aturan ...:") — a short word-run ending in a colon at the very start.
_LABEL_PREFIX = re.compile(r'^[A-Za-z][\w\s]{0,24}:\s*')

logger = logging.getLogger(__name__)

# Skip storing a candidate if an existing memory is this similar (semantic score 0..1).
_DEDUP_SCORE = 0.90

_EXTRACT_PROMPT = """Dari pertukaran chat ini, tentukan APAKAH ada fakta, preferensi, atau pelajaran DURABLE (berguna jangka panjang) tentang Apis, VPS-nya, atau cara kerja yang dia mau.

Apis: {user}
Emilia: {reply}

Aturan ketat:
- Kalau ADA hal durable → tulis SATU kalimat ringkas & faktual, sudut pandang pihak ketiga (contoh: "Apis lebih suka deploy pakai X", "Service Y jalan di port Z", "Kalau A, lakukan B").
- Kalau cuma basa-basi, sapaan, perintah sekali pakai, pertanyaan biasa, atau nggak ada yang layak diinget → balas PERSIS satu kata: SKIP
- JANGAN mengarang. JANGAN tulis apa pun selain kalimat fakta-nya, atau SKIP.

Jawaban:"""


async def maybe_capture(user_msg: str, reply: str) -> None:
    """Best-effort: extract one durable fact from an exchange and store it (deduped)."""
    if not config.DIFY_KB_ENABLED or not user_msg.strip() or not reply.strip():
        return
    try:
        raw = await conversation.oneshot(
            _EXTRACT_PROMPT.format(user=user_msg[:500], reply=reply[:500]),
            num_predict=120,
        )
    except Exception as e:
        logger.warning("memory_capture extract failed: %s", e)
        return

    cand = (raw or "").strip().splitlines()[0].strip(" \"'`-•").strip() if raw else ""
    cand = _LABEL_PREFIX.sub("", cand).strip()
    # reject non-facts / model noise
    if (not cand or "SKIP" in cand.upper() or len(cand) < 12 or len(cand) > 280
            or cand.lower().startswith(("maaf", "saya tidak", "tidak ada"))):
        return

    # dedup: don't store something we essentially already know
    try:
        hits = dify_kb.retrieve(cand, top_k=1)
        if hits and hits[0].get("score", 0) >= _DEDUP_SCORE:
            logger.info("memory_capture: duplicate, skip — %s", cand[:60])
            return
    except Exception:
        pass  # dedup is best-effort; proceed to store

    if dify_kb.ingest("learned", cand):
        logger.info("memory_capture: stored — %s", cand[:80])
