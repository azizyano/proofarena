//! ProofArena escrow — native NEAR, testnet-only MVP.
//!
//! STATUS: `IMPLEMENTED_UNTESTED`.
//! This file has **not been compiled or executed**: the build environment for
//! this iteration had no Rust toolchain and no network access to fetch
//! `near-sdk`. The executable reference implementation that *was* tested is
//! `proofarena/chain/engine.py`, and its 41 adversarial tests assert exactly the
//! rules encoded here. Do not mark this contract "done" until `cargo test`
//! output and a `near-sandbox` run exist (see `communications/STATUS_BOARD.md`).
//!
//! Design rules (mirrored in `docs/NEAR_ESCROW_SPEC.md` and the Python engine):
//!
//! * Only the bounty owner may approve, pay, refund, reconcile or dispute.
//! * One approved winner; `Open -> Review -> Approved -> PayoutPending -> Paid`.
//! * A settlement attempt is written **before** the transfer promise, so a
//!   missing callback can never be treated as a failure.
//! * The callback is `#[private]`; a foreign predecessor aborts.
//! * Ambiguous or missing callback => `NeedsReconciliation`; no automatic retry.
//! * `Paid` and `Refunded` are terminal; accounting is conserved per bounty.

use near_sdk::json_types::U128;
use near_sdk::{env, near, AccountId, Gas, NearToken, PanicOnDefault, Promise, PromiseResult};

/// Gas attached to the private settlement callback. Enough for a small state
/// write; the transfer itself does not need this budget.
const CALLBACK_GAS: Gas = Gas::from_tgas(25);

/// Storage reserve retained per bounty record (0.001 NEAR). Kept separate from
/// the reward liability so a payout can never consume storage funds.
const STORAGE_RESERVE_YOCTO: u128 = 1_000_000_000_000_000_000_000;

/// Hard cap for a single reward in the MVP (100 NEAR).
const MAX_REWARD_YOCTO: u128 = 100_000_000_000_000_000_000_000_000;

const MAX_TITLE_LEN: usize = 120;
const MAX_CLAIMS: usize = 32;
const MAX_ATTEMPTS: usize = 16;

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BountyState {
    Open,
    Review,
    Approved,
    PayoutPending,
    Paid,
    RefundPending,
    Refunded,
    NeedsReconciliation,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AttemptKind {
    Payout,
    Refund,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AttemptStatus {
    Pending,
    Succeeded,
    Failed,
    Ambiguous,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Attempt {
    pub attempt_id: u64,
    pub kind: AttemptKind,
    pub status: AttemptStatus,
    pub created_ns: u64,
    /// State to restore if the transfer fails explicitly.
    pub state_before: BountyState,
    pub resolved_ns: Option<u64>,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Claim {
    pub claim_id: u64,
    pub bounty_id: u64,
    pub submitter: AccountId,
    pub evidence_commitment_hex: String,
    pub manifest_sha256_hex: Option<String>,
    pub approved: bool,
    pub paid: bool,
    pub submitted_ns: u64,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Bounty {
    pub bounty_id: u64,
    pub owner: AccountId,
    pub title: String,
    pub reward_yocto: U128,
    pub metadata_hash_hex: String,
    pub deadline_ns: u64,
    pub review_deadline_ns: u64,
    pub created_ns: u64,
    pub state: BountyState,
    pub approved_claim_id: Option<u64>,
    pub winner: Option<AccountId>,
    pub paid_yocto: U128,
    pub refunded_yocto: U128,
    pub escrow_balance_yocto: U128,
    pub storage_reserve_yocto: U128,
    pub pending_attempt_id: Option<u64>,
    pub last_attempt_id: Option<u64>,
    pub disputed: bool,
    pub claims: Vec<u64>,
    pub attempts: Vec<Attempt>,
}

impl Bounty {
    fn is_terminal(&self) -> bool {
        matches!(self.state, BountyState::Paid | BountyState::Refunded)
    }

    fn assert_settleable(&self, action: &str) {
        assert!(!self.is_terminal(), "bounty is terminal; cannot {}", action);
        assert!(
            self.state != BountyState::NeedsReconciliation,
            "bounty needs reconciliation before {}",
            action
        );
        assert!(!self.disputed, "bounty is disputed; settlement is frozen");
    }

    fn check_invariants(&self) {
        let paid = self.paid_yocto.0;
        let refunded = self.refunded_yocto.0;
        let reward = self.reward_yocto.0;
        assert!(
            !(paid > 0 && refunded > 0),
            "invariant: bounty both paid and refunded"
        );
        assert!(
            paid + refunded <= reward,
            "invariant: settled exceeds funded reward"
        );
        assert_eq!(
            self.escrow_balance_yocto.0,
            reward - paid - refunded,
            "invariant: escrow balance drift"
        );
        if self.state == BountyState::Paid {
            assert_eq!(paid, reward, "invariant: Paid without full reward");
        }
        if self.state == BountyState::Refunded {
            assert_eq!(refunded, reward, "invariant: Refunded without full refund");
        }
        let pending = self
            .attempts
            .iter()
            .filter(|a| a.status == AttemptStatus::Pending)
            .count();
        assert!(pending <= 1, "invariant: more than one pending attempt");
        if self.is_terminal() {
            assert!(
                self.pending_attempt_id.is_none(),
                "invariant: terminal bounty has a pending attempt"
            );
        }
    }
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    pub owner: AccountId,
    pub retired: bool,
    pub bounties: near_sdk::store::IterableMap<u64, Bounty>,
    pub claims: near_sdk::store::IterableMap<u64, Claim>,
    pub next_bounty_id: u64,
    pub next_claim_id: u64,
    pub next_attempt_id: u64,
}

fn assert_hex64(value: &str, what: &str) {
    assert_eq!(value.len(), 64, "{} must be 64 hex characters", what);
    assert!(
        value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
        "{} must be lowercase hex",
        what
    );
}

#[near]
impl Contract {
    #[init]
    pub fn new(owner: AccountId) -> Self {
        Self {
            owner,
            retired: false,
            bounties: near_sdk::store::IterableMap::new(b"b"),
            claims: near_sdk::store::IterableMap::new(b"c"),
            next_bounty_id: 1,
            next_claim_id: 1,
            next_attempt_id: 1,
        }
    }

    // ------------------------------------------------------------------
    // funding
    // ------------------------------------------------------------------
    /// Create and fund a bounty. The attached deposit must equal
    /// `reward_yocto + STORAGE_RESERVE_YOCTO` exactly.
    #[payable]
    pub fn create_bounty(
        &mut self,
        title: String,
        metadata_hash_hex: String,
        reward_yocto: U128,
        deadline_ns: u64,
        review_deadline_ns: u64,
    ) -> u64 {
        assert!(!self.retired, "contract is retired");
        let title = title.trim().to_string();
        assert!(
            !title.is_empty() && title.chars().count() <= MAX_TITLE_LEN,
            "title must be 1..{} characters",
            MAX_TITLE_LEN
        );
        assert_eq!(
            metadata_hash_hex.len(),
            64,
            "metadata hash must be 64 hex characters"
        );
        assert_hex64(&metadata_hash_hex, "metadata hash");

        let reward = reward_yocto.0;
        assert!(reward > 0, "reward must be positive");
        assert!(reward <= MAX_REWARD_YOCTO, "reward exceeds MVP cap");

        let now = env::block_timestamp();
        assert!(deadline_ns > now, "submission deadline must be in the future");
        assert!(
            review_deadline_ns > deadline_ns,
            "review deadline must follow the submission deadline"
        );

        let required = reward
            .checked_add(STORAGE_RESERVE_YOCTO)
            .expect("deposit overflow");
        let attached = env::attached_deposit().as_yoctonear();
        assert_eq!(
            attached, required,
            "attached deposit must equal reward + storage reserve ({} yocto)",
            required
        );

        let owner = env::predecessor_account_id();
        let bounty_id = self.next_bounty_id;
        self.next_bounty_id += 1;

        let bounty = Bounty {
            bounty_id,
            owner: owner.clone(),
            title,
            reward_yocto: U128(reward),
            metadata_hash_hex,
            deadline_ns,
            review_deadline_ns,
            created_ns: now,
            state: BountyState::Open,
            approved_claim_id: None,
            winner: None,
            paid_yocto: U128(0),
            refunded_yocto: U128(0),
            escrow_balance_yocto: U128(reward),
            storage_reserve_yocto: U128(STORAGE_RESERVE_YOCTO),
            pending_attempt_id: None,
            last_attempt_id: None,
            disputed: false,
            claims: Vec::new(),
            attempts: Vec::new(),
        };
        bounty.check_invariants();
        self.bounties.insert(bounty_id, bounty);

        near_sdk::log!(
            "{{\"event\":\"bounty_created\",\"bounty_id\":{},\"owner\":\"{}\",\"reward_yocto\":\"{}\",\"metadata_hash_hex\":\"{}\"}}",
            bounty_id,
            owner,
            reward,
            env::block_timestamp()
        );
        bounty_id
    }

    // ------------------------------------------------------------------
    // claims
    // ------------------------------------------------------------------
    #[payable]
    pub fn commit_claim(
        &mut self,
        bounty_id: u64,
        evidence_commitment_hex: String,
        manifest_sha256_hex: Option<String>,
    ) -> u64 {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        assert_eq!(
            bounty.state,
            BountyState::Open,
            "claims are closed for this bounty"
        );
        assert!(
            env::block_timestamp() <= bounty.deadline_ns,
            "submission window has closed"
        );
        assert_hex64(&evidence_commitment_hex, "evidence commitment");
        if let Some(ref digest) = manifest_sha256_hex {
            assert_hex64(digest, "manifest digest");
        }

        for claim_id in bounty.claims.iter() {
            let existing = self.claims.get(claim_id).expect("claim index corrupt");
            assert_ne!(
                existing.evidence_commitment_hex, evidence_commitment_hex,
                "duplicate claim commitment"
            );
        }
        assert!(
            bounty.claims.len() < MAX_CLAIMS,
            "claim limit reached for this bounty"
        );

        let claim_id = self.next_claim_id;
        self.next_claim_id += 1;
        let claim = Claim {
            claim_id,
            bounty_id,
            submitter: env::predecessor_account_id(),
            evidence_commitment_hex,
            manifest_sha256_hex,
            approved: false,
            paid: false,
            submitted_ns: env::block_timestamp(),
        };
        bounty.claims.push(claim_id);
        self.claims.insert(claim_id, claim);
        self.bounties.insert(bounty_id, bounty);
        claim_id
    }

    pub fn mark_review(&mut self, bounty_id: u64) {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        assert_eq!(bounty.state, BountyState::Open, "only an Open bounty can move to Review");
        let caller = env::predecessor_account_id();
        assert!(
            caller == bounty.owner || env::block_timestamp() > bounty.deadline_ns,
            "only the owner may close submissions before the deadline"
        );
        assert!(
            bounty.pending_attempt_id.is_none(),
            "cannot change state while a settlement attempt is pending"
        );
        bounty.state = BountyState::Review;
        bounty.check_invariants();
        self.bounties.insert(bounty_id, bounty);
    }

    // ------------------------------------------------------------------
    // approval and payout
    // ------------------------------------------------------------------
    pub fn approve_claim(&mut self, bounty_id: u64, claim_id: u64) {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        bounty.assert_settleable("approve a claim");
        assert_eq!(env::predecessor_account_id(), bounty.owner, "only the owner may approve");
        assert_eq!(bounty.state, BountyState::Review, "approval requires Review state");
        assert!(
            env::block_timestamp() <= bounty.review_deadline_ns,
            "review window has closed"
        );
        assert!(bounty.approved_claim_id.is_none(), "a winner is already approved");

        let mut claim = self.claims.get(&claim_id).expect("unknown claim").clone();
        assert_eq!(claim.bounty_id, bounty_id, "claim does not belong to this bounty");
        assert!(!claim.approved, "claim is not eligible");

        claim.approved = true;
        bounty.approved_claim_id = Some(claim_id);
        bounty.winner = Some(claim.submitter.clone());
        bounty.state = BountyState::Approved;
        bounty.check_invariants();
        self.claims.insert(claim_id, claim);
        self.bounties.insert(bounty_id, bounty);
        near_sdk::log!(
            "{{\"event\":\"claim_approved\",\"bounty_id\":{},\"claim_id\":{},\"winner\":\"{}\"}}",
            bounty_id,
            claim_id,
            self.claims.get(&claim_id).expect("claim").submitter
        );
    }

    /// Reserve the reward and dispatch the transfer. State is committed
    /// (`PayoutPending` + attempt) before the promise is created.
    pub fn request_payout(&mut self, bounty_id: u64) -> u64 {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        bounty.assert_settleable("request payout");
        assert_eq!(env::predecessor_account_id(), bounty.owner, "only the owner may request payout");
        assert!(bounty.pending_attempt_id.is_none(), "a settlement attempt is already pending");
        assert_eq!(bounty.state, BountyState::Approved, "payout requires Approved state");
        assert!(
            !bounty
                .attempts
                .iter()
                .any(|a| a.kind == AttemptKind::Payout && a.status == AttemptStatus::Ambiguous),
            "a previous payout attempt is ambiguous; reconciliation is required"
        );
        assert!(bounty.attempts.len() < MAX_ATTEMPTS, "attempt limit reached");
        assert!(self.claim_of(&bounty).is_some(), "no approved claim to pay");

        let attempt_id = self.next_attempt_id;
        self.next_attempt_id += 1;
        bounty.attempts.push(Attempt {
            attempt_id,
            kind: AttemptKind::Payout,
            status: AttemptStatus::Pending,
            created_ns: env::block_timestamp(),
            state_before: bounty.state.clone(),
            resolved_ns: None,
        });
        bounty.pending_attempt_id = Some(attempt_id);
        bounty.last_attempt_id = Some(attempt_id);
        bounty.state = BountyState::PayoutPending;

        let winner = bounty.winner.clone().expect("approved bounty must have a winner");
        let amount = bounty.reward_yocto;
        bounty.check_invariants();
        self.bounties.insert(bounty_id, bounty);

        Promise::new(winner.clone())
            .transfer(NearToken::from_yoctonear(amount.0))
            .then(
                Promise::new(env::current_account_id()).on_payout_complete(
                    bounty_id,
                    attempt_id,
                    CALLBACK_GAS,
                ),
            );
        near_sdk::log!(
            "{{\"event\":\"payout_requested\",\"bounty_id\":{},\"attempt_id\":{},\"amount_yocto\":\"{}\",\"recipient\":\"{}\"}}",
            bounty_id,
            attempt_id,
            amount.0,
            winner
        );
        attempt_id
    }

    /// Private settlement callback. Only the contract can call this.
    #[private]
    pub fn on_payout_complete(&mut self, bounty_id: u64, attempt_id: u64) {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        assert_eq!(bounty.state, BountyState::PayoutPending, "no payout is pending");
        assert_eq!(
            bounty.pending_attempt_id,
            Some(attempt_id),
            "callback attempt is not the outstanding attempt"
        );
        let index = self.attempt_index(&bounty, attempt_id).expect("attempt not found");
        let now = env::block_timestamp();
        match env::promise_result(0) {
            PromiseResult::Successful(_) => {
                let reward = bounty.reward_yocto;
                let claim_id = bounty.approved_claim_id;
                bounty.attempts[index].status = AttemptStatus::Succeeded;
                bounty.attempts[index].resolved_ns = Some(now);
                bounty.paid_yocto = reward;
                bounty.escrow_balance_yocto = U128(0);
                bounty.pending_attempt_id = None;
                bounty.state = BountyState::Paid;
                if let Some(claim_id) = claim_id {
                    if let Some(mut claim) = self.claims.get(&claim_id).cloned() {
                        claim.paid = true;
                        self.claims.insert(claim_id, claim);
                    }
                }
                bounty.check_invariants();
                self.bounties.insert(bounty_id, bounty);
                near_sdk::log!(
                    "{{\"event\":\"payout_completed\",\"bounty_id\":{},\"attempt_id\":{}}}",
                    bounty_id,
                    attempt_id
                );
            }
            PromiseResult::Failed => {
                let restore = bounty.attempts[index].state_before.clone();
                bounty.attempts[index].status = AttemptStatus::Failed;
                bounty.attempts[index].resolved_ns = Some(now);
                bounty.pending_attempt_id = None;
                bounty.state = restore;
                bounty.check_invariants();
                self.bounties.insert(bounty_id, bounty);
                near_sdk::log!(
                    "{{\"event\":\"payout_failed\",\"bounty_id\":{},\"attempt_id\":{}}}",
                    bounty_id,
                    attempt_id
                );
            }
        }
    }

    // ------------------------------------------------------------------
    // refunds
    // ------------------------------------------------------------------
    pub fn refund_expired(&mut self, bounty_id: u64) -> u64 {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        bounty.assert_settleable("refund");
        assert_eq!(env::predecessor_account_id(), bounty.owner, "only the owner may refund");
        assert!(bounty.pending_attempt_id.is_none(), "a settlement attempt is already pending");
        assert!(
            matches!(bounty.state, BountyState::Open | BountyState::Review),
            "refund is not available from this state"
        );
        assert!(
            bounty.approved_claim_id.is_none(),
            "refund is refused: a claim has already been approved"
        );
        assert!(
            env::block_timestamp() > bounty.review_deadline_ns,
            "refund is not available until the review grace period ends"
        );
        assert!(bounty.attempts.len() < MAX_ATTEMPTS, "attempt limit reached");

        let attempt_id = self.next_attempt_id;
        self.next_attempt_id += 1;
        bounty.attempts.push(Attempt {
            attempt_id,
            kind: AttemptKind::Refund,
            status: AttemptStatus::Pending,
            created_ns: env::block_timestamp(),
            state_before: bounty.state.clone(),
            resolved_ns: None,
        });
        bounty.pending_attempt_id = Some(attempt_id);
        bounty.last_attempt_id = Some(attempt_id);
        bounty.state = BountyState::RefundPending;

        let owner = bounty.owner.clone();
        let amount = bounty.reward_yocto;
        bounty.check_invariants();
        self.bounties.insert(bounty_id, bounty);

        Promise::new(owner.clone())
            .transfer(NearToken::from_yoctonear(amount.0))
            .then(
                Promise::new(env::current_account_id()).on_refund_complete(
                    bounty_id,
                    attempt_id,
                    CALLBACK_GAS,
                ),
            );
        near_sdk::log!(
            "{{\"event\":\"refund_requested\",\"bounty_id\":{},\"attempt_id\":{},\"recipient\":\"{}\"}}",
            bounty_id,
            attempt_id,
            owner
        );
        attempt_id
    }

    #[private]
    pub fn on_refund_complete(&mut self, bounty_id: u64, attempt_id: u64) {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        assert_eq!(bounty.state, BountyState::RefundPending, "no refund is pending");
        assert_eq!(
            bounty.pending_attempt_id,
            Some(attempt_id),
            "callback attempt is not the outstanding attempt"
        );
        let index = self.attempt_index(&bounty, attempt_id).expect("attempt not found");
        let now = env::block_timestamp();
        match env::promise_result(0) {
            PromiseResult::Successful(_) => {
                let reward = bounty.reward_yocto;
                bounty.attempts[index].status = AttemptStatus::Succeeded;
                bounty.attempts[index].resolved_ns = Some(now);
                bounty.refunded_yocto = reward;
                bounty.escrow_balance_yocto = U128(0);
                bounty.pending_attempt_id = None;
                bounty.state = BountyState::Refunded;
                bounty.check_invariants();
                self.bounties.insert(bounty_id, bounty);
                near_sdk::log!(
                    "{{\"event\":\"refund_completed\",\"bounty_id\":{},\"attempt_id\":{}}}",
                    bounty_id,
                    attempt_id
                );
            }
            PromiseResult::Failed => {
                let restore = bounty.attempts[index].state_before.clone();
                bounty.attempts[index].status = AttemptStatus::Failed;
                bounty.attempts[index].resolved_ns = Some(now);
                bounty.pending_attempt_id = None;
                bounty.state = restore;
                bounty.check_invariants();
                self.bounties.insert(bounty_id, bounty);
            }
        }
    }

    // ------------------------------------------------------------------
    // reconciliation and dispute
    // ------------------------------------------------------------------
    /// Owner-signed resolution of a stuck transfer. The owner must have checked
    /// the real chain outcome; this method never guesses.
    pub fn reconcile(
        &mut self,
        bounty_id: u64,
        resolution: String,
        evidence_sha256: String,
        note: String,
    ) {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        assert_eq!(env::predecessor_account_id(), bounty.owner, "only the owner may reconcile");
        assert_eq!(
            bounty.state,
            BountyState::NeedsReconciliation,
            "reconciliation requires NeedsReconciliation"
        );
        assert_hex64(&evidence_sha256, "reconciliation evidence digest");
        assert!(!note.is_empty() && note.len() <= 512, "note must be 1..512 characters");

        if resolution == "paid" {
            bounty.paid_yocto = bounty.reward_yocto;
            bounty.escrow_balance_yocto = U128(0);
            bounty.state = BountyState::Paid;
            if let Some(claim_id) = bounty.approved_claim_id {
                if let Some(mut claim) = self.claims.get(&claim_id).cloned() {
                    claim.paid = true;
                    self.claims.insert(claim_id, claim);
                }
            }
        } else if resolution == "refunded" {
            bounty.refunded_yocto = bounty.reward_yocto;
            bounty.escrow_balance_yocto = U128(0);
            bounty.state = BountyState::Refunded;
        } else {
            env::panic_str("resolution must be 'paid' or 'refunded'");
        }
        for attempt in bounty.attempts.iter_mut() {
            if attempt.status == AttemptStatus::Ambiguous {
                attempt.resolved_ns = Some(env::block_timestamp());
                attempt.status = if resolution == "paid" {
                    AttemptStatus::Succeeded
                } else {
                    AttemptStatus::Failed
                };
            }
        }
        bounty.check_invariants();
        self.bounties.insert(bounty_id, bounty);
    }

    pub fn set_dispute(&mut self, bounty_id: u64, disputed: bool) {
        let mut bounty = self.bounties.get(&bounty_id).expect("unknown bounty").clone();
        assert_eq!(env::predecessor_account_id(), bounty.owner, "only the owner may dispute");
        assert!(
            !bounty.is_terminal(),
            "cannot change the dispute flag in a terminal state"
        );
        assert!(
            bounty.pending_attempt_id.is_none(),
            "cannot change the dispute flag during settlement"
        );
        bounty.disputed = disputed;
        bounty.check_invariants();
        self.bounties.insert(bounty_id, bounty);
    }

    // ------------------------------------------------------------------
    // views (sanitized: commitments and state only)
    // ------------------------------------------------------------------
    pub fn get_bounty(&self, bounty_id: u64) -> Bounty {
        self.bounties.get(&bounty_id).expect("unknown bounty").clone()
    }

    pub fn list_bounty_ids(&self) -> Vec<u64> {
        self.bounties.keys().copied().collect()
    }

    pub fn get_claim(&self, claim_id: u64) -> Claim {
        self.claims.get(&claim_id).expect("unknown claim").clone()
    }

    pub fn get_claims(&self, bounty_id: u64) -> Vec<Claim> {
        let bounty = self.bounties.get(&bounty_id).expect("unknown bounty");
        bounty
            .claims
            .iter()
            .map(|id| self.claims.get(id).expect("claim index corrupt").clone())
            .collect()
    }

    /// Total reward still held by the contract across all non-terminal bounties.
    pub fn total_escrow_yocto(&self) -> U128 {
        let mut total: u128 = 0;
        for bounty in self.bounties.values() {
            total = total.saturating_add(bounty.escrow_balance_yocto.0);
        }
        U128(total)
    }

    fn claim_of(&self, bounty: &Bounty) -> Option<Claim> {
        bounty
            .approved_claim_id
            .and_then(|id| self.claims.get(&id).cloned())
    }

    fn attempt_index(&self, bounty: &Bounty, attempt_id: u64) -> Option<usize> {
        bounty
            .attempts
            .iter()
            .position(|attempt| attempt.attempt_id == attempt_id)
    }
}

/// Tests mirroring SEC-001..SEC-010 of `plan/ACCEPTANCE_TESTS.md`.
///
/// They are written but **not executed here** (no Rust toolchain). Run
/// `cargo test` in `contracts/escrow` before trusting this contract.
#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::{accounts, VMContextBuilder};
    use near_sdk::testing_env;

    const ONE_NEAR: u128 = 1_000_000_000_000_000_000_000_000;

    fn setup() -> (Contract, VMContextBuilder) {
        let mut context = VMContextBuilder::new();
        context
            .current_account_id(accounts(0))
            .predecessor_account_id(accounts(0))
            .block_timestamp(1_000_000_000_000_000);
        testing_env!(context.build());
        (Contract::new(accounts(0)), context)
    }

    fn create_bounty(contract: &mut Contract, context: &mut VMContextBuilder, reward: u128) -> u64 {
        context.attached_deposit(NearToken::from_yoctonear(reward + STORAGE_RESERVE_YOCTO));
        testing_env!(context.build());
        contract.create_bounty(
            "Fixture bounty".to_string(),
            "a".repeat(64),
            U128(reward),
            context.block_timestamp() + 60_000_000_000,
            context.block_timestamp() + 120_000_000_000,
        )
    }

    #[test]
    fn creates_bounty_with_exact_deposit() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, 5 * ONE_NEAR);
        let bounty = contract.get_bounty(id);
        assert_eq!(bounty.escrow_balance_yocto.0, 5 * ONE_NEAR);
        assert_eq!(bounty.state, BountyState::Open);
    }

    #[test]
    #[should_panic(expected = "attached deposit must equal")]
    fn rejects_wrong_deposit() {
        let (mut contract, mut context) = setup();
        context.attached_deposit(NearToken::from_yoctonear(ONE_NEAR));
        testing_env!(context.build());
        contract.create_bounty(
            "Short funded".to_string(),
            "a".repeat(64),
            U128(5 * ONE_NEAR),
            context.block_timestamp() + 10,
            context.block_timestamp() + 20,
        );
    }

    #[test]
    #[should_panic(expected = "only the owner may approve")]
    fn non_owner_cannot_approve() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        context.predecessor_account_id(accounts(1));
        testing_env!(context.build());
        contract.commit_claim(id, "b".repeat(64), None);
        contract.mark_review(id);
        contract.approve_claim(id, 1);
    }

    #[test]
    #[should_panic(expected = "duplicate claim commitment")]
    fn rejects_duplicate_claim() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        contract.commit_claim(id, "b".repeat(64), None);
        contract.commit_claim(id, "b".repeat(64), None);
    }

    #[test]
    #[should_panic(expected = "refund is refused")]
    fn refund_after_approval_is_refused() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        contract.commit_claim(id, "b".repeat(64), None);
        contract.mark_review(id);
        contract.approve_claim(id, 1);
        context.block_timestamp(context.block_timestamp() + 1_000_000_000_000);
        testing_env!(context.build());
        contract.refund_expired(id);
    }

    #[test]
    #[should_panic(expected = "only the owner may request payout")]
    fn non_owner_cannot_request_payout() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        contract.commit_claim(id, "b".repeat(64), None);
        contract.mark_review(id);
        contract.approve_claim(id, 1);
        context.predecessor_account_id(accounts(2));
        testing_env!(context.build());
        contract.request_payout(id);
    }

    #[test]
    #[should_panic(expected = "a settlement attempt is already pending")]
    fn second_payout_request_is_refused() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        contract.commit_claim(id, "b".repeat(64), None);
        contract.mark_review(id);
        contract.approve_claim(id, 1);
        contract.request_payout(id);
        contract.request_payout(id);
    }

    #[test]
    #[should_panic(expected = "only the owner may refund")]
    fn non_owner_cannot_refund() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        context
            .predecessor_account_id(accounts(3))
            .block_timestamp(context.block_timestamp() + 1_000_000_000_000);
        testing_env!(context.build());
        contract.refund_expired(id);
    }

    #[test]
    #[should_panic(expected = "only an Open bounty can move to Review")]
    fn cannot_review_twice() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        contract.mark_review(id);
        contract.mark_review(id);
    }

    #[test]
    #[should_panic(expected = "bounty is disputed")]
    fn dispute_freezes_payout() {
        let (mut contract, mut context) = setup();
        let id = create_bounty(&mut contract, &mut context, ONE_NEAR);
        contract.commit_claim(id, "b".repeat(64), None);
        contract.mark_review(id);
        contract.approve_claim(id, 1);
        contract.set_dispute(id, true);
        contract.request_payout(id);
    }

    #[test]
    fn total_escrow_tracks_created_bounties() {
        let (mut contract, mut context) = setup();
        create_bounty(&mut contract, &mut context, ONE_NEAR);
        create_bounty(&mut contract, &mut context, 2 * ONE_NEAR);
        assert_eq!(contract.total_escrow_yocto().0, 3 * ONE_NEAR);
    }
}
