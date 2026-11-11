import base64, json, requests
RPC = "https://rpc.testnet.near.org"
ACCOUNT = "proofarena-c23922.testnet"
def post(method, params):
    return requests.post(RPC, json={"jsonrpc":"2.0","id":"x","method":method,"params":params}, timeout=40).json()

# full validation error -> list of accepted variants
for rt in ("call", "call_function"):
    args = base64.b64encode(b"{}").decode()
    res = post("query", {"request_type": rt, "finality":"final", "account_id": ACCOUNT,
                         "method_name":"list_bounty_ids","args_base64":args})
    print(f"request_type={rt!r} ->", "OK" if "result" in res else json.dumps(res.get("error"))[:400])
    if "result" in res:
        out = bytes(res["result"]["result"]).decode("utf-8","replace") if res["result"].get("result") else ""
        print("   result:", out, " logs:", res["result"].get("logs"))

# deployed code
vc = post("query", {"request_type":"view_code","finality":"final","account_id":ACCOUNT})
if "result" in vc:
    code = base64.b64decode(vc["result"]["code_base64"])
    print("\nview_code: bytes", len(code), "magic", code[:4], "hash", vc["result"]["hash"])
else:
    print("\nview_code error:", json.dumps(vc.get("error"))[:200])
