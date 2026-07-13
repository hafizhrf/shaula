"""
Shared registry of the running Discord clients.

Emilia (Hermes persona / ops) and Shaula (Claude Code executor) run as two discord
clients in ONE process so they share in-memory session state (the busy-guard and
routing must see the same `claude_session`). This module lets either client — and the
task executor in commands/task.py — resolve a channel under the right client and decide
who a message is addressed to, without import cycles.
"""
emilia = None          # DevOpsBot instance (set on startup)
shaula = None          # ShaulaBot instance (set on startup; None if Shaula disabled)
emilia_user_id = None  # captured on Emilia's on_ready
shaula_user_id = None  # captured on Shaula's on_ready


def shaula_channel(channel):
    """Return the same channel bound to Shaula's client, so messages post AS Shaula.
    Falls back to the given channel if Shaula isn't running (single-bot mode)."""
    if shaula is None or channel is None:
        return channel
    return shaula.get_channel(channel.id) or channel


def addressed_to_emilia(message) -> bool:
    """True if the message is talking TO Emilia (text contains 'emilia' or @mentions her).
    Used by both clients to split a thread: addressed → Emilia, otherwise → Shaula (Claude)."""
    content = (message.content or "").lower()
    if "emilia" in content:
        return True
    if emilia_user_id is not None and any(u.id == emilia_user_id for u in message.mentions):
        return True
    return False
