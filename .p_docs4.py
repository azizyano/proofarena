import pathlib

# ---------- README verdict ----------
p = pathlib.Path("README.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""| NEAR testnet account + signer | **VERIFIED ON CHAIN** — account funded, a real testnet tx executed (see `docs/TESTNET_EVIDENCE.md`) |
| NEAR testnet contract deployment | **AWAITING OWNER RUN** — needs `cargo test` + `deploy.py` (toolchain + bandwidth) |
| Overall | **READY FOR TESTNET DEPLOYMENT** — follow `docs/TESTNET_RUNBOOK.md` |""",
"""| NEAR testnet contract | **DEPLOYED** — `proofarena-c23922.testnet`, 230 KB wasm, code hash `3KcVAjtB…` |
| On-chain lifecycle | **VERIFIED_TESTNET** — fund → claim → verify → approve → payout, exactly once |
| On-chain refund path | **VERIFIED_TESTNET** — expired bounty refunded after the grace period |
| Overall | **VERIFIED ON NEAR TESTNET** — receipts and caveats in `docs/TESTNET_EVIDENCE.md` |""")
s = s.replace("""| Local end-to-end workflow | **TESTED_LOCAL** — 124 automated tests + `scripts/e2e_demo.py` |""",
"""| Local end-to-end workflow | **TESTED_LOCAL** — 156 automated tests + `scripts/e2e_demo.py` |""")
s = s.replace("python -m pytest tests/ -q          # expect: 124 passed", "python -m pytest tests/ -q          # expect: 156 passed")
s = s.replace("python -m pytest tests/ -q      # 124 passed", "python -m pytest tests/ -q      # 156 passed")
s = s.replace("# 1. Verify everything (124 tests: escrow invariants, runner isolation, API, web, NEAR plumbing)",
              "# 1. Verify everything (156 tests: escrow invariants, runner isolation, API, web, NEAR plumbing)")
s = s.replace("  tests/                   124 tests", "  tests/                   156 tests")
p.write_text(s, encoding="utf-8")
print("README updated")

# ---------- STATUS_BOARD ----------
p = pathlib.Path("communications/STATUS_BOARD.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""| PA-017 | P0 | NEAR/DevOps | BLOCKED (partially verified) |""",
"""| PA-017 | P0 | NEAR/DevOps | VERIFIED_TESTNET |""")
s = s.replace("""| 42 offline tests + 10 account tests; **testnet account funded** (`proofarena-c23922.testnet`); **a real testnet tx executed and is final** (`3E3cn3gfbJw9Lho4RgB5ntUy7QLcEieUkJ5kjwfQk22A`, `CodeDoesNotExist` = encoding accepted); contract not yet deployed | B6 + B7 |""",
"""| contract deployed (`9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61`); full lifecycle on chain (fund → claim → approve → payout once); refund path on chain; 6+ explorer-verifiable tx hashes in `docs/TESTNET_EVIDENCE.md` | — |""")
s = s.replace("""| PA-018 | P0 | CEO/PM | DONE (verdict) | release verdict in README | `READY FOR TESTNET DEMO`, not `VERIFIED_TESTNET` | — |""",
"""| PA-018 | P0 | CEO/PM | DONE (verdict) | release verdict in README | `VERIFIED_TESTNET` — payout and refund both observed on chain; production still out of scope | — |""")
s = s.replace("""| B6 | Testnet deployment has not been executed | PA-017 stays BLOCKED until the owner runs the runbook | follow `docs/TESTNET_RUNBOOK.md` (7 steps) |
| B7 | Build link measured at 11–19 KB/s | a 137 MB toolchain would take 2–3.5 h, so compilation must run on the owner's machine | run `cargo test` / `deploy.py` locally |""",
"""| B6 | ~~Testnet deployment not executed~~ | closed 2026-11-11: contract deployed and lifecycles verified | — |
| B7 | ~~Slow build link~~ | closed: built on the owner's machine | — |""")
s = s.replace("""python -m pytest tests/ -q   →   134 passed  (escrow, runner, API, web, signer, NEAR plumbing, account)""",
"""python -m pytest tests/ -q   →   156 passed  (escrow, runner, API, web, signer, NEAR plumbing, account, build config)""")
s = s.replace("""| 2 | ~~Testnet account + key~~ | done: `proofarena-c23922.testnet` | account id + funded balance |""",
"""| 2 | ~~Testnet account + key~~ | done: `proofarena-c23922.testnet` + `coldlemon3275.testnet` + auditor account | account ids, funded balances |""")
s = s.replace("""| 5 | Deploy | `python scripts/testnet/deploy.py --dry-run` then `--yes` | deploy tx hash |""",
"""| 5 | Deploy | `python scripts/testnet/deploy.py --dry-run` then `--yes` | deploy tx `9bK3NTS9…` |""")
s = s.replace("""| 7 | On-chain lifecycle | `python scripts/testnet/smoke.py` | 6 tx hashes + explorer links |""",
"""| 7 | On-chain lifecycle | `python scripts/testnet/smoke.py` + `refund_demo.py` | 7 tx hashes + explorer links |""")
p.write_text(s, encoding="utf-8")
print("STATUS_BOARD updated")

# ---------- QA matrix ----------
p = pathlib.Path("docs/QA_MATRIX.md")
s = p.read_text(encoding="utf-8")
s = s.replace("""→ 134 passed       (escrow, runner, API, web, signer, NEAR plumbing, account bootstrap)""",
              """→ 156 passed       (escrow, runner, API, web, signer, NEAR plumbing, account, build config)""")
s = s.replace("""| Contract deployed | **NOT RUN** | wasm never compiled here (toolchain download ~15 KB/s) |
| On-chain lifecycle (create/approve/payout) | **NOT RUN** | needs the deployed contract |""",
"""| Contract deployed | **PASS** | [`9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61`](https://explorer.testnet.near.org/transactions/9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61), code hash `3KcVAjtB…` |
| On-chain lifecycle: fund → claim → review → approve → payout | **PASS** | bounty #3, tx `4WzDB89V…`, `2Kpw6rLZ…`, `FpwgPgaq…`, `Dfzioifs…`, `AVwQZ4md…` |
| Payout settled exactly once, callback observed | **PASS** | `payout_completed` log inside `AVwQZ4md…`; state `Paid`, escrow 0 |
| Second payout refused on chain | **PASS** | `409 E_STATE` from the contract |
| On-chain refund after grace period | **PASS** | bounty #4, `H5eGNida…`; state `Refunded` |
| Early refund refused on chain | **PASS** | `409 E_STATE`, no transaction emitted |""")
p.write_text(s, encoding="utf-8")
print("QA_MATRIX updated")

# ---------- status page gate ----------
p = pathlib.Path("proofarena/templates/status.html")
s = p.read_text(encoding="utf-8")
s = s.replace("""      <tr><td>G3 testnet deployment</td><td><span class="badge" data-state="Review">READY</span></td><td class="small">adapter + signer + deploy/smoke scripts tested offline; awaiting owner run (<code>docs/TESTNET_RUNBOOK.md</code>)</td></tr>""",
"""      <tr><td>G3 testnet deployment</td><td><span class="badge" data-state="Review">READY</span></td><td class="small">deployed at <code>proofarena-c23922.testnet</code>; run the app with <code>PROOFARENA_CHAIN_MODE=near-testnet</code></td></tr>""")
p.write_text(s, encoding="utf-8")
print("status page updated")
