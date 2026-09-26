"""Push expanded dataset to a bot URL, tick on the 30 test pairs, write submission.jsonl."""
import glob, json, sys, requests
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
def push(scope, cid, p):
    r = requests.post(f"{URL}/v1/context", json={"scope": scope, "context_id": cid, "version": 1, "payload": p, "delivered_at": "2026-04-26T10:00:00Z"})
    assert r.status_code == 200, r.text
for f in glob.glob("expanded/categories/*.json"):
    p = json.load(open(f)); push("category", p["slug"], p)
for f in glob.glob("expanded/merchants/*.json"):
    p = json.load(open(f)); push("merchant", p["merchant_id"], p)
for f in glob.glob("expanded/customers/*.json"):
    p = json.load(open(f)); push("customer", p["customer_id"], p)
for f in glob.glob("expanded/triggers/*.json"):
    p = json.load(open(f)); push("trigger", p["id"], p)
print(requests.get(f"{URL}/v1/healthz").json())
pairs = json.load(open("expanded/test_pairs.json"))["pairs"]
out = []
for pr in pairs:
    r = requests.post(f"{URL}/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [pr["trigger_id"]]}, timeout=60).json()
    a = r["actions"][0] if r["actions"] else {}
    out.append({"test_id": pr["test_id"], **{k: a.get(k) for k in ("body", "cta", "send_as", "suppression_key", "rationale")}})
    print(pr["test_id"], a.get("send_as"), a.get("cta"), "\n ", a.get("body"), "\n")
open("submission.jsonl", "w").write("\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n")
