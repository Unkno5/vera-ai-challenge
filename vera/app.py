import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import replies
from .composer import compose
from .llm import model_name

app = FastAPI()
START = time.time()
SCOPES = ("category", "merchant", "customer", "trigger")
contexts: dict = {}
conversations: dict = {}
sent_suppression: set = set()
pool = ThreadPoolExecutor(max_workers=3)


def _get(scope, cid):
    return (contexts.get((scope, cid)) or {}).get("payload")


@app.get("/v1/healthz")
def healthz():
    counts = {s: 0 for s in SCOPES}
    for (s, _) in contexts:
        counts[s] += 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}


@app.get("/v1/metadata")
def metadata():
    return {"team_name": "Prakhar Jaiswal", "team_members": ["Prakhar Jaiswal"], "model": model_name(),
            "approach": "fact-pack + trigger-aware LLM composer with validation, template fallback, "
                        "rule-based reply router (auto-reply/intent/hostile/off-topic)",
            "contact_email": "prakharjaiswalpj2002@gmail.com", "version": "1.0.0",
            "submitted_at": datetime.now(timezone.utc).isoformat()}


class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: Optional[str] = None


@app.post("/v1/context")
def push_context(b: CtxBody):
    if b.scope not in SCOPES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope",
                                                      "details": f"scope must be one of {SCOPES}"})
    key = (b.scope, b.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= b.version:
        if cur["version"] == b.version:
            return {"accepted": True, "ack_id": f"ack_{b.context_id}_v{b.version}",
                    "stored_at": datetime.now(timezone.utc).isoformat()}
        return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version",
                                                      "current_version": cur["version"]})
    contexts[key] = {"version": b.version, "payload": b.payload}
    return {"accepted": True, "ack_id": f"ack_{b.context_id}_v{b.version}",
            "stored_at": datetime.now(timezone.utc).isoformat()}


class TickBody(BaseModel):
    now: Optional[str] = None
    available_triggers: list[str] = []


def _parse(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


@app.post("/v1/tick")
def tick(b: TickBody):
    now = _parse(b.now) if b.now else None
    cands = []
    for tid in b.available_triggers:
        trg = _get("trigger", tid)
        if not trg:
            continue
        exp = _parse(trg.get("expires_at") or "")
        if now and exp and exp < now:
            continue
        sk = trg.get("suppression_key") or tid
        if sk in sent_suppression or tid in sent_suppression:
            continue
        m = _get("merchant", trg.get("merchant_id"))
        cat = _get("category", (m or {}).get("category_slug"))
        if not (m and cat):
            continue
        cust = _get("customer", trg.get("customer_id")) if trg.get("customer_id") else None
        if trg.get("scope") == "customer" and not cust:
            continue  # cannot message a customer we know nothing about
        cands.append((trg.get("urgency", 1), tid, trg, m, cat, cust))
    cands.sort(key=lambda x: -x[0])
    chosen, seen_m = [], set()
    for c in cands:
        if c[2]["merchant_id"] in seen_m:
            continue
        seen_m.add(c[2]["merchant_id"])
        chosen.append(c)
    chosen = chosen[:20]

    def work(c):
        _, tid, trg, m, cat, cust = c
        msg = compose(cat, m, trg, cust)
        cid = f"conv_{m['merchant_id']}_{tid}"
        conversations[cid] = {"turns": [{"from": "bot", "msg": msg["body"]}], "bot_bodies": [msg["body"]],
                              "opening": msg["body"], "merchant_id": m["merchant_id"],
                              "trigger_summary": {"kind": trg.get("kind"), "payload": trg.get("payload")}}
        name = m["identity"].get("owner_first_name") or m["identity"]["name"]
        return {"conversation_id": cid, "merchant_id": m["merchant_id"], "customer_id": trg.get("customer_id"),
                "send_as": msg["send_as"], "trigger_id": tid,
                "template_name": f"vera_{trg.get('kind','generic')}_v1",
                "template_params": [name, trg.get("kind", ""), msg["body"][:100]],
                "body": msg["body"], "cta": msg["cta"], "suppression_key": msg["suppression_key"],
                "rationale": msg["rationale"]}

    futs = [(c, pool.submit(work, c)) for c in chosen]
    actions = []
    deadline = time.time() + 26
    for c, f in futs:
        try:
            a = f.result(timeout=max(0.1, deadline - time.time()))
            actions.append(a)
            sent_suppression.add(c[2].get("suppression_key") or c[1])
        except Exception:
            pass
    return {"actions": actions}


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: Optional[str] = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: Optional[int] = None


@app.post("/v1/reply")
def reply(b: ReplyBody):
    conv = conversations.setdefault(b.conversation_id, {"turns": [], "bot_bodies": []})
    if conv.get("ended"):
        return {"action": "end", "rationale": "Conversation already closed"}
    mid = b.merchant_id or conv.get("merchant_id")
    m = _get("merchant", mid) if mid else None
    cat = _get("category", (m or {}).get("category_slug")) if m else None
    return replies.respond(conv, m, cat, b.message)


@app.post("/v1/teardown")
def teardown():
    contexts.clear(); conversations.clear(); sent_suppression.clear()
    return {"ok": True}
