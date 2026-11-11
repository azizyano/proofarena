import pathlib

p = pathlib.Path("docs/TESTNET_EVIDENCE.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""## 4. Verify it yourself""",
"""## 3b. API ↔ signer ↔ chain wiring (live, executed)

With `wallet/near_signer.py` and `PROOFARENA_CHAIN_MODE=near-testnet scripts/dev.py` running:

| Step | Result |
|---|---|
| `GET /api/meta` | `chain_mode=near-testnet`, `chain_is_simulated=false`, contract `proofarena-c23922.testnet` |
| `GET /health` | `status=ok` |
| landing page | simulator banner correctly **absent** in testnet mode |
| `POST /auth/register` | **200** — the on-chain ownership check (`view_access_key`) accepted the NEAR key |
| `POST /auth/challenge` → `/sign` → `/auth/verify` | **200**, session for `proofarena-c23922.testnet` |
| `POST /v1/challenges` | **201**, manifest + digest created |
| `POST /v1/intents` (fund_bounty) → sign → `/fund` | **503 `E_CHAIN_UNAVAILABLE`** with `CodeDoesNotExist` — a real transaction was signed by the signer, broadcast, and rejected because no contract is deployed |

Signer audit log (real, no key material):

```json
{"kind":"signed","purpose":"login"}
{"kind":"signed","purpose":"intent","action":"fund_bounty"}
{"kind":"failed","method":"create_bounty","deposit_yocto":"101000000000000000000000",
 "error":"... CompilationError CodeDoesNotExist ..."}
```

So the entire application path is wired to testnet. The only missing piece is the contract bytecode.

## 4. Verify it yourself""")
p.write_text(s, encoding="utf-8")

p = pathlib.Path("docs/QA_MATRIX.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""| Contract deployed | **NOT RUN** | wasm never compiled here (toolchain download too slow) |""",
"""| API in `near-testnet` mode boots, banner suppressed | PASS | live `/api/meta` (`chain_is_simulated=false`), `/health` |
| Signer refuses/forwards calls correctly | PASS | live: register 200, fund 503 `CodeDoesNotExist`, audit log entries |
| Contract deployed | **NOT RUN** | wasm never compiled here (toolchain download ~15 KB/s) |""")
p.write_text(s, encoding="utf-8")
print("docs updated")
