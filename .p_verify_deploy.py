import base64, json, requests

RPC = "https://rpc.testnet.near.org"
def post(method, params):
    r = requests.post(RPC, json={"jsonrpc":"2.0","id":"x","method":method,"params":params}, timeout=40)
    return r.json()

TX = "9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61"
res = post("tx", {"tx_hash": TX, "sender_account_id": "proofarena-c23922.testnet", "wait_until": "FINAL"})
if "error" in res:
    print("lookup with that sender failed, trying the other account")
    res = post("tx", {"tx_hash": TX, "sender_account_id": "coldlemon3275.testnet", "wait_until": "FINAL"})
if "error" in res:
    print("ERROR:", json.dumps(res["error"])[:300]); raise SystemExit(1)

r = res["result"]
tx = r.get("transaction", {})
print("signer   :", tx.get("signer_id"))
print("receiver :", tx.get("receiver_id"))
print("final    :", r.get("final_execution_status"))
print("status   :", json.dumps(r.get("status"))[:200])
print("actions  :")
for a in tx.get("actions", []):
    if "DeployContract" in a:
        code = base64.b64decode(a["DeployContract"]["code"])
        print(f"  DeployContract: {len(code)/1024:.1f} KB, magic={code[:4]!r}, sha256={__import__('hashlib').sha256(code).hexdigest()[:16]}")
    elif "FunctionCall" in a:
        fc = a["FunctionCall"]
        args = base64.b64decode(fc["args"]).decode("utf-8", "replace")
        print(f"  FunctionCall  : {fc['method_name']}({args}) gas={fc['gas']} deposit={fc['deposit']}")
    else:
        print("  ", list(a.keys()))
for o in r.get("receipts_outcome", []):
    logs = o["outcome"].get("logs") or []
    for log in logs:
        print("  log:", log)
