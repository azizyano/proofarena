import base64, hashlib, json, requests
RPC = "https://rpc.testnet.near.org"
def post(method, params):
    return requests.post(RPC, json={"jsonrpc":"2.0","id":"x","method":method,"params":params}, timeout=40).json()

TX = "9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61"
r = post("tx", {"tx_hash": TX, "sender_account_id": "proofarena-c23922.testnet", "wait_until": "FINAL"})["result"]
code_b64 = r["transaction"]["actions"][0]["DeployContract"]["code"]
print("code field length:", len(code_b64))
print("code field prefix:", code_b64[:60])
raw = base64.b64decode(code_b64)
print("decoded bytes:", len(raw))
print("first 16 hex:", raw[:16].hex())
print("starts with wasm magic:", raw[:4] == b"\x00asm")

account = "proofarena-c23922.testnet"
acc = post("query", {"request_type":"view_account","finality":"final","account_id":account})["result"]
print("\naccount code_hash:", acc["code_hash"])
print("account storage:", acc["storage_usage"], "balance:", int(acc["amount"])/10**24)
for method in ("list_bounty_ids", "total_escrow_yocto"):
    view = post("query", {"request_type":"call","finality":"final","account_id":account,
                          "method_name":method,"args_base64": base64.b64encode(b"{}").decode()})
    if "error" in view:
        print(f"{method}: ERROR {json.dumps(view['error'])[:160]}")
    else:
        res = view["result"]
        txt = bytes(res["result"]).decode("utf-8", "replace") if res.get("result") else ""
        print(f"{method}: {txt[:120]}   logs={res.get('logs')}")
