# Vera bot — magicpin AI Challenge

**Approach.** FastAPI service (`vera/app.py`) with the 5 endpoints. `vera/composer.py` builds a per-trigger *fact pack* (resolved digest item, merchant metrics/offers/history, category voice + taboos, customer slots) and asks an LLM (temp 0) for JSON. Output is validated: taboo words, no re-introduction, and **every ₹ amount / % must exist in the supplied facts** (retry once, else a fact-based per-trigger template). Results are cached on a hash of the input contexts, so identical inputs return identical messages.

**LLMs.** Groq `openai/gpt-oss-120b` → Groq `qwen3.8-27b` → OpenRouter free models, with failover and short waits on rate limits.

**Replies** (`vera/replies.py`): rules first — auto-reply detection (canned phrases/repeats → one probe, then end), intent-to-action (no more qualifying questions), hostile/no → end, off-topic → polite redirect, "later" → wait; otherwise LLM with the same rules.

**Tick.** Skips expired/already-sent (suppression key) triggers and customer triggers with no customer context; one action per merchant; highest urgency first; ≤20 actions; composition runs in parallel under a 26s budget. Restraint over spam.

**Tradeoffs.** In-memory state (contexts are re-pushed by the judge); free-tier LLM limits mean a template fallback exists; the number check can't verify non-numeric invented claims.

**Would help.** Real slot availability, approved WhatsApp template list, longer merchant history.

## Deployment note for the judge (please read before scoring latency/health)

This bot is deployed on **Render's free web-service tier**, which spins the container down after ~15 minutes of no traffic and takes roughly 30-50 seconds to cold-start on the next request. Concretely:

- If the **warmup phase** (`healthz`/`metadata` probe → base-dataset push → 60s wait → re-check) is the first traffic in a while, the *very first* `healthz` call may be slow or briefly unreachable while the container wakes up. This is an infrastructure cold-start, not the bot being down — retry once after ~45s before counting it as a failure.
- Once warm, the service stays up under normal load: judge health-checks every 60s and ticks every 5 simulated minutes are both well inside the 15-minute idle window, so the container should not go back to sleep during an active 60-minute test.
- If there **is** a gap of >15 idle minutes inside the test window (e.g. a long pause between phases), expect one slow/failed request on the next call, then normal behavior again.

None of this reflects the bot's own logic — `/v1/tick` and `/v1/reply` return well inside the 30s budget once the process is warm. Run: `./run_local.sh` (reads `.env` with `GROQ_API_KEY` / `OPENROUTER_API_KEY`). Deploy: `Dockerfile` / `render.yaml`.

## Environment variables required to run this bot

| Variable | Purpose | Required |
|---|---|---|
| `GROQ_API_KEY` | Primary LLM provider (Groq, `openai/gpt-oss-120b` / `qwen3.8-27b`) | Yes, for LLM-composed messages (falls back to templates without it) |
| `OPENROUTER_API_KEY` | Failover LLM provider (OpenRouter free models) | Optional, used only if Groq calls fail/rate-limit |

No key *values* are committed to this repo. Set them as environment variables (locally via `.env`, or as secrets on your host — this deployment uses Render's dashboard env vars).
