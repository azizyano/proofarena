---
title: "ProofArena — Verifiable AI Security Work with NEAR Escrow"
subtitle: "Technical Whitepaper | Draft v0.1"
author: "ProofArena Project — AI-Assisted Delivery Specification"
date: "11 November 2026"
---

# ProofArena — Verifiable AI Security Work with NEAR Escrow

**Technical whitepaper — v0.1 | November 11, 2026**

*Status: design proposal for an authorized security-work marketplace; not audited, deployed, or operational.*

## Abstract

Generative AI makes vulnerability research and software patches cheaper to propose, but proposals are not verified results. Today's security workflows often separate discovery, demonstration, remediation, review, and compensation. A report can be plausible but not reproducible; a patch can look convincing while leaving the exploit path open; a bounty payer can have uncertainty over the evidence necessary to reward work.

**ProofArena** proposes a bounded coordination and payment protocol: a project owner publishes an authorized security challenge and locks a reward; autonomous auditor and defender agents contribute findings and fixes; an isolated verification service records reproducible test evidence; the human project owner approves the winner; a NEAR escrow contract settles the approved native-token reward. In the MVP, the underlying code is a synthetic benchmark fixture and all funds are testnet tokens.

This is **not** a cryptographic proof that code is secure, nor a way to delegate legal authorization to an LLM. It is a workflow that makes actions, responsibilities, and evidence auditable.

## 1. The problem

Three failures commonly undermine AI-assisted security work:

1. **Evidence gap:** a model reports an issue without demonstrating a reachable, economically meaningful failure of a code invariant.
2. **Remediation gap:** an attractive patch lacks regression tests or introduces other behavior changes.
3. **Settlement gap:** the report, review decision, payment and dispute history are recorded in different systems with inconsistent trust assumptions.

There is also a safety gap: indiscriminate automated testing against unknown targets can be unauthorized, unsafe and legally problematic. ProofArena starts with permissioned fixtures and explicit project-owner authorization.

## 2. Product thesis

A bounty should reward **work accepted against an agreed verification standard**, not the confidence of an AI agent's prose. A maintainer should be able to ask: What invariant failed? Which source revision and environment reproduced it? Did the proposed fix eliminate the tested failure? Who authorized release of funds? Can anyone pay twice or refund after approval?

ProofArena aligns these answers with a single challenge identifier and immutable on-chain settlement events, while keeping exploit details private.

## 3. Who uses the system?

| Actor | Goal | Authority |
|:--|:--|:--|
| Project owner / maintainer | Define challenge and reward; review work | Creates and funds bounty; approves claim in signed transaction |
| Auditor agent | Discover reachable invariant violation | May propose findings; cannot approve or transfer funds |
| Defender agent | Deliver patch and tests | May submit patch artifacts; cannot approve its own work |
| Independent verifier | Reproduce issue and confirm patch outcome | Produces evidence, not an on-chain spending signature |
| Platform operator | Host orchestration and the UI | Administers off-chain infrastructure; cannot unilaterally spend escrow |
| Reviewer / dispute operator | Investigate disputed results | MVP is human/manual, subject to explicit contract policy |

A user can operate multiple agents, but **verification independence** is a policy objective, not automatically guaranteed by their names. MVP labels independence honestly; future versions could use external validators and attestations.

## 4. Lifecycle and protocol

### 4.1 Challenge definition

The owner specifies a repository and immutable source revision, scope restrictions, target invariants or problem statement, authorized execution boundaries, reward amount, submission deadline, and review period. A metadata file is canonicalized and hashed; its digest is recorded on chain. The file itself remains off-chain so that the challenge can include sensitive information and flexible metadata.

### 4.2 Funding and commitment

Owner sends native NEAR **testnet** tokens in a payable bounty-creation call. Contract records owner, deposit accounting, deadlines, reward, and metadata hash. The contract must distinguish reward funds from storage and operational reserves; no payout may exceed unencumbered escrow for the bounty.

### 4.3 Research and evidence

The auditor produces (a) source revision, (b) a minimal deterministic failure test, (c) actual command and exit code, (d) observed-versus-expected behavior, (e) authorization scope and impact statement. A salted hash commitment can anchor the submission without publishing exploit content.

**A hash does not validate a finding.** Re-execution by a trusted, isolated verifier is required. A finding that merely demonstrates a mathematical property with no reachable state transition is categorized as research, not accepted as a security impact.

### 4.4 Remediation

A defender produces an exact patch and a regression test. The verifier checks baseline failure, patch success, non-regression, and whether the test was meaningfully exercised. The owner retains the right to reject an undesirable fix even if tests pass.

### 4.5 Review and payment

A report with artifact digests, runner version, commands and test outcomes is attached off-chain; only a non-sensitive digest/status is eligible for public anchoring. The owner signs an on-chain `approve_claim` action. The escrow moves into `PayoutPending` before sending a transfer promise; a private callback records success/failure. A failed callback is an exceptional reconciliation problem, **not** permission for blind retry.

### 4.6 Disclosure

Security reports remain encrypted or access-controlled and are never automatically placed on public IPFS or transaction arguments. The owner controls disclosure timing through a coordinated disclosure policy.

## 5. Formal trust boundaries

The first-party smart contract trusts NEAR consensus for account signatures and execution. It **does not** trust the web server, LLM, verifier logs, or repository metadata to authorize payment. Off-chain verification produces *evidence* for an owner's decision.

- **On-chain:** bounty identity, owner, reward balance, deadlines, metadata commitment, claim reference, payout/refund settlement state, transaction events.
- **Off-chain:** source code and fixtures, exploit demonstrations, encrypted evidence, AI reasoning, build logs, test results, patch diffs, reviewer comments.
- **Bridge:** content-addressed evidence manifests anchored by a digest; UI shows both digest and human-readable result, with no false claim that an on-chain hash proves test correctness.

The MVP is **permissioned and semi-centralized**. That is an explicit design choice rather than concealed decentralization.

## 6. Core invariants

Let `D_b` be a bounty's funded net reward, `Paid_b` its finalized reward payout and `Refund_b` its finalized refund, all measured in native testnet token units (yoctoNEAR). For every bounty `b`:

1. **Conservation:** `Paid_b + Refund_b <= D_b` (separate storage reserve, gas and explicitly accounted fees).
2. **Mutual exclusion:** no bounty finalizes both a reward payment and refund.
3. **Uniqueness:** at most one approved winning claim per MVP bounty.
4. **Authorization:** only the owner account may approve a claim, subject to agreed deadlines/conditions.
5. **State integrity:** `Paid` and `Refunded` are terminal; no additional transfer can originate from that bounty.
6. **No duplicate settlement:** transfer initiation must be unique; retries require a verified prior failure and must not overlap outstanding calls.
7. **Privacy:** public records contain digests and references, not raw vulnerability details or secrets.
8. **Scope:** no verifier may execute an unknown third-party target without explicit permission and policy.

On NEAR, transfers and callbacks are asynchronous. A successful initiating transaction does not mean the outgoing transfer finalized. The implementation must model pending and exceptional states, including callback failures and independent reconciliation.

## 7. Architecture

A Next.js frontend connects owner wallets to a NEAR contract and displays challenge/evidence state. FastAPI hosts permissioned challenge APIs, agent coordination and verification jobs. PostgreSQL stores non-secret metadata, artifact digests and audit events. Large encrypted artifacts live in private object storage or a local private store for the demo. A queue schedules isolated workers; the runner denies outbound network access and privileged host access. Rust/near-sdk implements native-NEAR escrow; local Sandbox tests and NEAR testnet demonstrate transfers.

```text
Owner wallet -> Next.js UI -> NEAR Escrow (signed tx)
                         -> FastAPI -> PostgreSQL / private artifact store
                                      -> Queue -> isolated audit runner
                                               -> independent verifier result
                                                  -> owner reviews in UI
```

Agents use typed tool interfaces; none receives platform treasury keys, owner wallet seed phrases or unrestricted shell authority.

## 8. Verification model and confidence

The standard report contains a challenge ID, immutable source commit, isolated runtime image digest, baseline test command and exit code, patched revision and test command, a concise impact trace, artifact hashes, a human or verifier-agent review decision, and confidence/limitations. Three classes exist:

- **Reproduced:** verifier independently observed invariant failure in the authorized fixture.
- **Remediated:** verifier observed failure on baseline and success on patched revision, with selected regression suite passing.
- **Inconclusive:** evidence is missing, flaky, out of scope, or environment-dependent; no automatic reward approval.

These are evidence levels, not severity classifications and not formal program proofs.

## 9. Incentives and economics

**MVP:** no platform fee; owner funds one native testnet reward, one eligible claim may be paid after approval. No token is launched. Agents are not paid merely for API calls or verbosity.

**Future paid pilot (hypothesis):** owners sponsor bounties; platform could charge an agreed completion fee (e.g., a proposed 5% scenario) or a subscription for private continuous verification. This rate is **an assumption for business testing**, not a committed or validated market price. A sustainable pilot must measure bounty fill rate, reproduction rate, time to patch, accepted reward rate, false-positive rate, and customer retention.

Avoid token incentives during discovery: they create sybil and spam pressures before the evidence standard works.

## 10. Competitive positioning

Projects listed on NEARHunt illustrate relevant mechanics: funded PR bounties (NEARPORTAL), swarms-for-hire and escrow (Taskbond), executable bug evidence (Antibody), confidential exploit demonstration (Sealed Exploit), and patch competition (SwarmShop). ProofArena's proposed difference is an **integrated** permissioned finding-to-patch-to-approval-to-settlement process with explicit verification and no autonomous AI custody. They remain independent projects; no affiliation or code reuse is claimed. See `docs/RESEARCH_SOURCES.md`.

## 11. Threats and mitigation priorities

| Threat | MVP control | Residual risk |
|:--|:--|:--|
| Duplicate payout or refund race | Explicit payout/refund states; no concurrent settlement; callback checks | Unresolved callbacks require manual reconciliation |
| Malicious repository / build script | Network-disabled, non-root, no host secrets, resource-limited runner | Container escape and supply-chain risk remain |
| LLM prompt injection from repo files | Repository contents are data; tool allowlists; scoped agent permissions | Agents may still be influenced; require human gates |
| Falsified test output | Verifier reruns exact commands and captures signed/provenance logs | Operator can still lie absent independent attestations |
| Unwanted exploit disclosure | Private encrypted artifacts; on-chain digests only | Privacy depends on service configuration |
| Colluding auditor and verifier | Owner approval, reproducible evidence | Owner can make mistaken decisions |
| Owner denial of payment | Objective criteria, escrowed funds, appeal process later | MVP retains owner veto; not trustless adjudication |

A production launch requires independent smart contract audit, incident procedures, privacy/legal review and threat-model validation.

## 12. Delivery roadmap

**Hackathon sprint (stretch):** Day 1 contracts/schema and demo fixture; Day 2 auditor/defender workflow; Day 3 independent verification + wallet approval + transaction display; Day 4 integration tests, short demo, README, release proof. Missing external integration may be mocked **only if clearly labeled**; the escrow payout itself must be tested on a real NEAR Sandbox or testnet to claim functionality.

**30 days:** pilot with 2–3 authorized repositories, reviewer controls, more robust verifier isolation and tracing, stable audit logs.

**90 days:** invited security researchers, third-party verifier attestations, formalized dispute workflow, optional NEP-141 token escrow only after additional audit.

## 13. Limitations and open questions

- Automated tests cannot prove all vulnerabilities are eliminated.
- A centrally run verifier is not cryptographically trustless.
- A blockchain records payment state, not legal scope or real-world disclosure compliance.
- Claim quality/scoring and reviewer disputes are partly subjective.
- Native NEAR escrow is an MVP constraint; cross-chain swaps and stablecoins are excluded.
- Published hackathon project descriptions do not establish competitor code quality or market traction.
- Exact contest rules, submission cutoff and technology requirements must be checked by an organizer-controlled source before submitting.

## 14. Conclusion

ProofArena's core insight is to make **verification the economic boundary**: useful security work creates reproducible and reviewable artifacts; only an authorized human decision releases escrow. The long-term goal is an open market for AI-assisted security work. The first milestone is deliberately smaller: one honest, demonstrable transaction that maps research evidence to a real approval and an on-chain payment.

## References

1. [NEAR smart contract quickstart](https://docs.near.org/smart-contracts/quickstart) — Rust contracts, Sandbox testing, testnet workflow.
2. [NEAR cross-contract calls](https://docs.near.org/smart-contracts/anatomy/crosscontract) — asynchronous promises, callback failures and security implications.
3. [NEARHunt project board](https://nearhunt.xyz/) — community submissions and inspirations, accessed 2026-11-11.
4. [Research references and inspiration mapping](docs/RESEARCH_SOURCES.md).
