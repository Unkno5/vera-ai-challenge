"""Multi-turn reply logic: rules first (auto-reply, intent, hostile, off-topic), LLM second."""
import json
import re

from .llm import chat_json

AUTO = re.compile(
    r"automated assistant|auto[- ]?reply|automatic reply|thank you for contacting|thanks for contacting|"
    r"aapki jaankari ke liye|team tak pahu|hamari team|will get back to you|we will respond|"
    r"our team will|business hours|currently unavailable|out of office|main ek automated|"
    r"dhanyavaad.*sampark|shukriya.*sampark", re.I)
INTENT = re.compile(
    r"\b(yes|yeah|yep|ok(ay)?|sure|go ahead|do it|let'?s do it|lets go|proceed|confirm|done|haan|ha|kar do|"
    r"karo|chalega|theek hai|thik hai|send( it| me)?|join|sign me up|mujhe .*judrna|i want to join|start)\b", re.I)
NO = re.compile(r"\b(not interested|no thanks|stop|unsubscribe|don'?t message|do not message|leave me alone|"
                r"nahi chahiye|mat bhejo|band karo|remove me)\b", re.I)
ABUSE = re.compile(r"\b(idiot|stupid|useless|fuck|shit|bakwas|chutiya|spam|scam|harass)\b", re.I)
OFFTOPIC = re.compile(r"\b(gst|income tax|itr|loan|visa|passport|cricket score|weather|politics|legal notice)\b", re.I)
LATER = re.compile(r"\b(later|busy|not now|baad mein|abhi nahi|tomorrow|kal|next week|call me|in a meeting)\b", re.I)
HINDI = re.compile(r"\b(hai|hain|kya|nahi|aap|mujhe|karo|kar|haan|chahiye|batao|bhejo|theek|abhi)\b", re.I)


def _hi(text, merchant):
    return bool(HINDI.search(text)) or "hi" in ((merchant or {}).get("identity", {}).get("languages") or [])


def respond(conv, merchant, category, message):
    """conv: dict with turns list, auto_count, last_bot_bodies. Returns reply action dict."""
    msg = (message or "").strip()
    conv.setdefault("turns", []).append({"from": "merchant", "msg": msg})
    hi = _hi(msg, merchant)
    name = ((merchant or {}).get("identity", {}).get("owner_first_name")
            or (merchant or {}).get("identity", {}).get("name", ""))

    # auto-reply: canned phrase, or same message repeated
    prev = [t["msg"] for t in conv["turns"][:-1] if t["from"] == "merchant"]
    if AUTO.search(msg) or prev.count(msg) >= 1:
        conv["auto_count"] = conv.get("auto_count", 0) + 1
        if conv["auto_count"] == 1:
            body = (f"Lagta hai yeh auto-reply hai. {name} ji, jab owner free hon to bas 'YES' likh dijiye — "
                    "main setup kar dungi." if hi else
                    f"Looks like an auto-reply. When the owner is free, just reply YES and I'll set it up.")
            return _send(conv, body, "binary_yes_stop", "Auto-reply detected; one probe for the human owner")
        return _end(conv, "Auto-reply repeated; exiting gracefully to avoid wasting turns")

    if ABUSE.search(msg) and not OFFTOPIC.search(msg):
        return _end(conv, "Hostile merchant; polite exit")
    if NO.search(msg):
        return _end(conv, "Merchant declined; graceful exit")
    if OFFTOPIC.search(msg):
        body = ("Iske liye main help nahi kar sakti — yeh mere scope se bahar hai. Aapke magicpin listing ke liye "
                "hum jahan the wahin se continue karein? Bas 'YES' likhiye." if hi else
                "That's outside what I can help with — I'm focused on your magicpin listing and growth. "
                "Shall we continue where we left off? Reply YES.")
        return _send(conv, body, "binary_yes_stop", "Off-topic; declined politely and redirected")
    if LATER.search(msg) and not INTENT.search(msg):
        conv["turns"].append({"from": "bot", "msg": "[wait]"})
        return {"action": "wait", "wait_seconds": 3600, "rationale": "Merchant asked for time; backing off 1h"}

    # LLM composition with rules in prompt (intent -> action mode)
    body = _llm_reply(conv, merchant, category, msg, hi)
    if not body:
        body = _canned(msg, hi, name, conv)
    # anti-repetition
    if body in conv.get("bot_bodies", []):
        body += " Batayiye, kaunsa hissa pehle karun?" if hi else " Which part should I start with?"
    return _send(conv, body, "open_ended", "Advanced conversation based on merchant reply")


def _canned(msg, hi, name, conv):
    if INTENT.search(msg):
        return (f"Done {name} ji — maine shuru kar diya hai, draft ready hote hi yahin bhejti hoon. Kuch add karna ho to bata dijiye."
                if hi else f"On it, {name} — starting now. I'll send the draft here shortly; tell me if you want anything added.")
    return ("Samajh gayi. Thoda detail bataiye — main uske hisaab se draft bana deti hoon."
            if hi else "Got it. Share a bit more detail and I'll draft it for you right away.")


def _llm_reply(conv, merchant, category, msg, hi):
    sys = ("You are Vera, magicpin's merchant assistant, replying on WhatsApp. Rules: If the merchant shows intent "
           "(yes/ok/go ahead/let's do it/join) switch IMMEDIATELY to action mode: confirm what you're doing and give the "
           "concrete next step or draft; NEVER ask another qualifying question. If they ask a question, answer it using "
           "only the given facts (don't invent). Keep 1-3 short sentences, match their language (Hinglish if they use Hindi), "
           "no re-introduction, one CTA at end at most. Category voice must be respected; no taboo words. "
           'Output JSON {"body": str}.')
    facts = {"merchant": {k: (merchant or {}).get(k) for k in ("identity", "offers", "performance", "signals")},
             "category_voice": (category or {}).get("voice"),
             "opening_message": conv.get("opening"), "trigger": conv.get("trigger_summary"),
             "conversation": conv["turns"][-8:]}
    try:
        b = (chat_json(sys, json.dumps(facts, ensure_ascii=False), 400) or {}).get("body")
        return b.strip() if isinstance(b, str) and len(b.strip()) > 5 else None
    except Exception:
        return None


def _send(conv, body, cta, why):
    conv["turns"].append({"from": "bot", "msg": body})
    conv.setdefault("bot_bodies", []).append(body)
    return {"action": "send", "body": body, "cta": cta, "rationale": why}


def _end(conv, why):
    conv["ended"] = True
    return {"action": "end", "rationale": why}
