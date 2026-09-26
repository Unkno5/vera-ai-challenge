# Vera bot — magicpin AI Challenge

**Approach.** FastAPI service (`vera/app.py`) with the 5 endpoints. `vera/composer.py` builds a per-trigger *fact pack* (resolved digest item, merchant metrics/offers/history, category voice + taboos, customer slots) and asks an LLM (temp 0) for JSON. Output is validated: taboo words, no re-introduction, and **every ₹ amount / % must exist in the supplied facts** (retry once, else a fact-based per-trigger template). Results are cached on a hash of the input contexts, so identical inputs return identical messages.

**LLMs.** Groq `openai/gpt-oss-120b` → Groq `qwen3.8-27b` → OpenRouter free models, with failover and short waits on rate limits.

**Replies** (`vera/replies.py`): rules first — auto-reply detection (canned phrases/repeats → one probe, then end), intent-to-action (no more qualifying questions), hostile/no → end, off-topic → polite redirect, "later" → wait; otherwise LLM with the same rules.

**Tick.** Skips expired/already-sent (suppression key) triggers and customer triggers with no customer context; one action per merchant; highest urgency first; ≤20 actions; composition runs in parallel under a 26s budget. Restraint over spam.

**Tradeoffs.** In-memory state (contexts are re-pushed by the judge); free-tier LLM limits mean a template fallback exists; the number check can't verify non-numeric invented claims.

**Would help.** Real slot availability, approved WhatsApp template list, longer merchant history.

Run: `./run_local.sh` (reads `.env` with GROQ_API_KEY / OPENROUTER_API_KEY). Deploy: `Dockerfile` / `render.yaml`.
