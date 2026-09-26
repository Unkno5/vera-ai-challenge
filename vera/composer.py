"""compose(category, merchant, trigger, customer) -> message dict.

LLM (Groq, temperature 0) writes from a curated fact pack; validation + a
deterministic template fallback guarantee a valid, non-hallucinated result.
"""
import json
import os
import re

from .llm import chat_json, model_name
MODEL = model_name()
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "12"))

def _pct(x):
    return f"{abs(x) * 100:.0f}%"


def find_digest(category, item_id):
    for d in category.get("digest", []) or []:
        if d.get("id") == item_id:
            return d
    return None


def fact_pack(category, merchant, trigger, customer):
    ident = merchant.get("identity", {})
    payload = dict(trigger.get("payload", {}))
    pack = {
        "trigger": {"kind": trigger.get("kind"), "urgency": trigger.get("urgency"),
                    "payload": payload, "expires_at": trigger.get("expires_at")},
        "merchant": {
            "name": ident.get("name"), "owner_first_name": ident.get("owner_first_name"),
            "city": ident.get("city"), "locality": ident.get("locality"),
            "languages": ident.get("languages"), "verified": ident.get("verified"),
            "subscription": merchant.get("subscription"),
            "performance_30d": merchant.get("performance"),
            "active_offers": [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"],
            "expired_offers": [o["title"] for o in merchant.get("offers", []) if o.get("status") != "active"],
            "customer_aggregate": merchant.get("customer_aggregate"),
            "signals": merchant.get("signals"),
            "review_themes": merchant.get("review_themes"),
            "recent_history": [{"from": h.get("from"), "body": (h.get("body") or "")[:200]} for h in (merchant.get("conversation_history") or [])[-3:]],
        },
        "category": {
            "slug": category.get("slug"),
            "voice": {k: (category.get("voice") or {}).get(k) for k in ("tone", "code_mix", "vocab_taboo")},
            "peer_stats": {k: v for k, v in (category.get("peer_stats") or {}).items() if k in ("avg_ctr", "avg_views_30d", "avg_calls_30d", "avg_rating", "retention_6mo_pct")},
            "offer_catalog": [o["title"] for o in category.get("offer_catalog", [])][:8],
            "seasonal_beats": (category.get("seasonal_beats") or [])[:3],
            "trend_signals": (category.get("trend_signals") or [])[:3],
        },
    }
    top = payload.get("top_item_id") or payload.get("digest_item_id") or payload.get("alert_id")
    d = find_digest(category, top) if top else None
    if d:
        pack["digest_item"] = d
    elif category.get("digest"):
        pack["other_digest_items"] = [
            {k: x.get(k) for k in ("id", "title", "source", "summary")} for x in category["digest"][:3]]
    pack["audience"] = "customer (write TO the customer, as the merchant)" if (customer or trigger.get("scope") == "customer") else "merchant (write TO the merchant, as Vera)"
    if customer:
        pack["customer"] = {k: customer.get(k) for k in ("identity", "relationship", "state", "preferences")}
    return pack


SYSTEM = """You write WhatsApp messages for magicpin's merchant assistant "Vera".
Output ONLY JSON: {"body": str, "cta": "binary_yes_stop"|"open_ended"|"multi_choice_slots"|"none", "rationale": str}.

Hard rules:
- Use ONLY facts in the provided FACTS JSON. Never invent numbers, offers, sources, competitors, dates or names.
- Anchor on 1-2 concrete verifiable facts (numbers, dates, source citation, peer stat, merchant's own metric). State clearly WHY NOW (the trigger).
- Offers: use service+price form from the merchant's/catalog offers ("Haircut @ ₹99"), never generic "X% off".
- Voice: follow category voice (dentists/pharmacies = clinical peer, no hype; salons/gyms/restaurants = warm, practical). Never use taboo words. No exclamation-heavy promo tone.
- Language: if merchant/customer languages include hi (or language_pref hi-en mix) write natural Hinglish (Roman script) mixed with English; else English.
- Address merchant by name (Dr. {first name} for dentists, else owner first name / business name). No greeting fluff, no self-introduction, no "I hope you're well".
- Use a compulsion lever: curiosity, loss aversion, social proof (only if data present), effort externalization ("I've drafted X — say go"), or asking the merchant a specific question.
- Exactly ONE call-to-action, placed in the LAST sentence. Action triggers -> a single YES-style ask ("Reply YES"). Pure info triggers -> a soft open question or none.
- FACTS.audience says who the message is addressed to; address ONLY that person (customer messages must not mention the merchant's dashboard metrics or address the owner).
- Concise: 2-5 short sentences. If FACTS has "customer": message is sent as the merchant to that customer (send on merchant's behalf, first person plural "we"/clinic name), use customer name, honour preferred slots, use real slots/offers from trigger payload, multi-slot choice allowed.
- NEVER invent prices, programs, perks (e.g. free gifts), durations or statistics that are not literally in FACTS. If unsure, leave it out and ask the merchant instead.
- Never show raw field names or ids (e.g. d_2026W17_x, estimated_uplift_pct, shelf_action_recommended); write plain human language. Always address the merchant by name.
- Never claim actions already done; promise only things Vera can do (draft, pull, set up, send)."""


def _llm(pack):
    return chat_json(SYSTEM, "FACTS:\n" + json.dumps(pack, ensure_ascii=False))


_NUM = re.compile(r"₹\s?[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?\s?%")


def _numset(text):
    vals = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            vals.add(round(float(m.replace(",", "")), 4))
        except ValueError:
            pass
    return vals


def _grounded(body, pack):
    """Every ₹ amount / percentage in the body must exist in the facts (percent may be a 0-1 fraction)."""
    facts = _numset(json.dumps(pack, ensure_ascii=False))
    for tok in _NUM.findall(body):
        v = round(float(re.sub(r"[^\d.]", "", tok)), 4)
        if v in facts:
            continue
        if tok.endswith("%") and (round(v / 100, 4) in facts or any(abs(f * 100 - v) < 0.6 for f in facts if f < 1.5)):
            continue
        return False
    return True


def _valid(out, category, customer):
    body = (out or {}).get("body")
    if not isinstance(body, str) or len(body.strip()) < 20:
        return False
    low = body.lower()
    for w in (category.get("voice", {}).get("vocab_taboo") or []):
        w = re.sub(r"\(.*?\)", "", w).strip().lower()
        if w and w in low:
            return False
    if re.search(r"\bvera here\b|i hope you|my name is", low):
        return False
    return True


def _first(merchant):
    ident = merchant["identity"]
    f = ident.get("owner_first_name") or ident.get("name", "").split()[0]
    return f"Dr. {f}" if merchant.get("category_slug") == "dentists" else f


def fallback(category, merchant, trigger, customer):
    """Deterministic template; only uses supplied facts."""
    name = _first(merchant)
    kind = trigger.get("kind", "")
    p = trigger.get("payload", {})
    perf = merchant.get("performance", {})
    hi = "hi" in (merchant["identity"].get("languages") or [])
    offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    offer = offers[0] if offers else None
    cta, send_as = "open_ended", "vera"
    d = find_digest(category, p.get("top_item_id") or p.get("digest_item_id") or p.get("alert_id"))

    if customer:
        send_as = "merchant_on_behalf"
        cname = customer["identity"]["name"]
        slots = p.get("available_slots") or p.get("next_session_options") or []
        sl = " ya ".join(s["label"] for s in slots[:2])
        body = f"Hi {cname}, {merchant['identity']['name']} here. "
        if kind == "recall_due":
            body += f"Aapki {p.get('service_due','recall').replace('_',' ')} due ho gayi hai"
        else:
            body += f"Aapki {kind.replace('_',' ')} ka time aa gaya hai"
        if sl:
            body += f" — slots ready hain: {sl}."
            cta = "multi_choice_slots"
        else:
            body += "."
        if offer:
            body += f" {offer}."
        body += " Reply karein ya apna preferred time batayein."
    elif d:
        body = f"{name}, {d['title']} ({d.get('source','')}). {d.get('summary','')} Want me to pull the details and draft a note for your patients/customers?"
    elif kind == "festival_upcoming" and p.get("festival"):
        body = f"{name}, {p['festival']} is on {p.get('date')} ({p.get('days_until')} days away)." + (f" Want me to draft a festive post around your live offer {offer} — reply YES?" if offer else " Want me to draft a festive post for your listing — reply YES?")
        cta = "binary_yes_stop"
    elif kind == "milestone_reached" and p.get("value_now"):
        body = f"{name}, you're at {p['value_now']} {str(p.get('metric','')).replace('_',' ')}, just {p.get('milestone_value',0) - p['value_now']} short of {p.get('milestone_value')}. Want me to draft a message asking your recent happy customers for a review — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "review_theme_emerged" and p.get("theme"):
        body = f"{name}, {p.get('occurrences_30d')} reviews in 30 days mention {str(p['theme']).replace('_',' ')}" + (f" (\"{p['common_quote']}\")" if p.get("common_quote") else "") + ". Want me to draft a reply you can post to those reviews — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "curious_ask_due":
        body = f"{name}, quick question to tailor your next post: which service is most in demand at {merchant['identity']['name']} this week?"
    elif kind == "dormant_with_vera" and p.get("days_since_last_merchant_message"):
        body = f"{name}, it's been {p['days_since_last_merchant_message']} days since we last spoke" + (f" (last topic: {str(p['last_topic']).replace('_',' ')})" if p.get("last_topic") else "") + f". Your 30-day numbers: {perf.get('views')} views, {perf.get('calls')} calls. Want a 2-minute refresh of your listing — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "gbp_unverified":
        body = f"{name}, your Google profile isn't verified yet — verification via {str(p.get('verification_path','postcard or phone')).replace('_',' ')} typically lifts visibility (est. +{_pct(p.get('estimated_uplift_pct',0))}). Want me to start it — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "ipl_match_today" and p.get("match"):
        body = f"{name}, {p['match']} is on tonight at {p.get('venue')}." + (f" Want me to push {offer} in a match-night post — reply YES?" if offer else " Want me to draft a match-night post — reply YES?")
        cta = "binary_yes_stop"
    elif kind in ("perf_dip", "seasonal_perf_dip"):
        body = f"{name}, {p.get('metric','views')} {_pct(p.get('delta_pct',0))} down over the last {p.get('window','7d')}" + (f" (30d views {perf.get('views')}, calls {perf.get('calls')})" if perf else "") + ". Want me to set up a quick fix — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "perf_spike":
        body = f"{name}, {p.get('metric','calls')} up {_pct(p.get('delta_pct',0))} this week. Want me to double down while it's working — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "renewal_due":
        body = f"{name}, your {p.get('plan','')} plan has {p.get('days_remaining')} days left (₹{p.get('renewal_amount')}). Renew now to keep your listing live — reply YES?"
        cta = "binary_yes_stop"
    elif kind == "competitor_opened" and p.get("competitor_name"):
        body = f"{name}, {p.get('competitor_name')} opened {p.get('distance_km')}km away with {p.get('their_offer')}. Want me to sharpen your offer" + (f" ({offer})" if offer else "") + " — reply YES?"
        cta = "binary_yes_stop"
    else:
        facts = ", ".join(f"{k.replace('_',' ')}: {v}" for k, v in p.items()
                          if isinstance(v, (str, int, float)) and not str(k).endswith(("_id", "iso")) and k not in ("placeholder", "metric or topic", "metric_or_topic"))[:160]
        body = f"{name}, {kind.replace('_',' ')}" + (f" ({facts})" if facts else "") + (f". Your live offer {offer} fits here" if offer else "") + ". Want me to draft the next step for you — reply YES?"
        cta = "binary_yes_stop"
    return {"body": body, "cta": cta, "rationale": f"Template fallback for {kind}", "send_as": send_as}


_CACHE = {}


def compose(category, merchant, trigger, customer=None):
    """Deterministic per identical inputs: results are cached on a hash of the full input contexts."""
    key = json.dumps([category, merchant, trigger, customer], sort_keys=True, ensure_ascii=False, default=str)
    if key in _CACHE:
        return dict(_CACHE[key])
    pack = fact_pack(category, merchant, trigger, customer)
    out = None
    for attempt in range(2):
        try:
            out = _llm(pack if attempt == 0 else {**pack, "REMINDER": "Use only numbers/prices copied verbatim from FACTS."})
        except Exception:
            out = None
        if _valid(out, category, customer) and _grounded(out["body"], pack):
            break
        out = None
    used_fallback = out is None
    if used_fallback:
        out = fallback(category, merchant, trigger, customer)
    res = {
        "body": out["body"].strip(),
        "cta": out.get("cta") if out.get("cta") in ("binary_yes_stop", "open_ended", "multi_choice_slots", "none") else "open_ended",
        "send_as": "merchant_on_behalf" if customer or trigger.get("scope") == "customer" else "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": out.get("rationale") or f"Composed for trigger {trigger.get('kind')}",
    }
    if not used_fallback:  # don't pin a fallback result; a later call may succeed with the LLM
        _CACHE[key] = res
    return dict(res)
