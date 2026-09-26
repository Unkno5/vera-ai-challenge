"""Optional multi-turn handler: respond(state, merchant_message) -> action dict."""
from vera.replies import respond as _respond


def respond(state: dict, merchant_message: str) -> dict:
    return _respond(state, state.get("merchant"), state.get("category"), merchant_message)
