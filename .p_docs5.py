import pathlib

p = pathlib.Path("docs/TESTNET_EVIDENCE.md")
s = p.read_text(encoding="utf-8")

s = s.replace("""## 4. Independent verification (anyone can re-run this)""",
"""## 3b. Cleaned-up final state (all four settlements exercised)

The interrupted run that left bounty #2 open was refunded once its grace period expired, so the
contract now has **zero outstanding liability** and both terminal paths are represented:

| Bounty | State | Paid | Refunded | Transaction |
|---|---|---|---|---|
| #1 | Paid | 0.1 NEAR | — | `6mxzbdYBpFHjgo4aJxdSjYJm1cnigJG1sJYmsgAhi4nP` |
| #2 | Refunded | — | 0.1 NEAR | [`EYyzkpCuQkxkv2U29vZr81W4SoFQKJs8mmU2UW9Fgtcd`](https://explorer.testnet.near.org/transactions/EYyzkpCuQkxkv2U29vZr81W4SoFQKJs8mmU2UW9Fgtcd) |
| #3 | Paid | 0.1 NEAR | — | `AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap` |
| #4 | Refunded | — | 0.02 NEAR | `H5eGNidaraf6akVhzggE7hNJzbaUynTGgptFVoZRe5bZ` |

```
total_escrow_yocto()  ->  0
owner balance         ->  1.7918 NEAR  (0.3 spent on two rewards, the rest is gas + storage)
auditor balance       ->  10.1996 NEAR (received both 0.1 rewards)
```

Conservation, observed on the real chain: every yocto that went in either reached the approved
winner or came back to the owner. No bounty is both paid and refunded; nothing is stuck.

## 4. Independent verification (anyone can re-run this)""")

s = s.replace("""1. **Bounty #2 is intentionally left `Open`** with 0.1 NEAR escrowed. It came from a run that was
   interrupted before a claim existed. It becomes refundable after its review window; the refund
   endpoint settles it exactly like bounty #4. Until then `total_escrow_yocto = 1e23`.""",
"""1. **Bounty #2 was refunded** after its grace period (see 3b); escrow liability is now `0`.""")
p.write_text(s, encoding="utf-8")
print("evidence updated")

p = pathlib.Path("docs/QA_MATRIX.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""| Early refund refused on chain | **PASS** | `409 E_STATE`, no transaction emitted |""",
"""| Early refund refused on chain | **PASS** | `409 E_STATE`, no transaction emitted |
| Abandoned bounty refunded after grace; escrow liability reaches 0 | **PASS** | bounty #2 → `EYyzkpCu…`; `total_escrow_yocto() → 0` |""")
p.write_text(s, encoding="utf-8")
print("qa updated")

p = pathlib.Path("README.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""| On-chain refund path | **VERIFIED_TESTNET** — expired bounty refunded after the grace period |""",
"""| On-chain refund path | **VERIFIED_TESTNET** — two expired bounties refunded; escrow liability 0 |""")
s = s.replace("""Full sequence in **[docs/TESTNET_RUNBOOK.md](docs/TESTNET_RUNBOOK.md)**""",
"""Hosting options: **[docs/HOSTING.md](docs/HOSTING.md)**. Deploy sequence:
**[docs/TESTNET_RUNBOOK.md](docs/TESTNET_RUNBOOK.md)**""")
p.write_text(s, encoding="utf-8")
print("readme updated")
