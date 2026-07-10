### Title
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` Are Unconditionally Blocked by `#[pause]` With No Bypass Role, Preventing Downstream Contracts From Completing Time-Sensitive BTC Verification - (`contract/src/lib.rs`)

---

### Summary

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` carry a plain `#[pause]` attribute with no `except(roles(...))` exemption. When the `BtcLightClient` contract is paused, every caller — including privileged accounts — is blocked from invoking these functions. Any downstream NEAR contract (bridge, atomic swap, escrow) that depends on a cross-contract call to either function to release funds or complete a time-locked operation will be permanently stalled for the duration of the pause, potentially causing irreversible financial loss to users who have already broadcast their BTC transaction.

---

### Finding Description

The `Role` enum defines dedicated bypass roles for the two other paused functions:

- `UnrestrictedSubmitBlocks` — lets privileged accounts call `submit_blocks` even when paused
- `UnrestrictedRunGC` — lets privileged accounts call `run_mainchain_gc` even when paused [1](#0-0) 

No equivalent role exists for the verification functions. Both are decorated with a bare `#[pause]` that carries no `except` clause:

```rust
#[pause]
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }

#[pause]
pub fn verify_transaction_inclusion_v2(&self, ...) -> bool { ... }
``` [2](#0-1) [3](#0-2) 

Compare this with `run_mainchain_gc`, which correctly uses `#[pause(except(roles(Role::UnrestrictedRunGC)))]` to preserve privileged access during a pause: [4](#0-3) 

The `near-plugins` `#[pause]` macro injects a guard that panics unconditionally for any caller when the contract is paused and no `except` clause is present. Because `verify_transaction_inclusion_v2` internally calls `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())`, both entry points are blocked simultaneously. [5](#0-4) 

---

### Impact Explanation

The `BtcLightClient` is explicitly designed to be consumed by downstream NEAR contracts as an SPV oracle — a recipient contract calls `verify_transaction_inclusion_v2` via a cross-contract call to confirm a BTC payment before releasing NEAR-side assets. If the light client is paused while such an operation is in flight:

1. The cross-contract call to `verify_transaction_inclusion_v2` panics.
2. The downstream contract's release/claim logic cannot proceed.
3. If the downstream protocol has a time-bounded claim window (atomic swap timeout, bridge escrow deadline), the window expires while the user is unable to act.
4. The user has already broadcast and confirmed their BTC transaction on-chain but cannot claim the corresponding NEAR-side value — a direct, irreversible financial loss.

This is structurally identical to M-02: a pause on a user-protective action (debt repayment / BTC proof submission) causes harm to accumulate (liquidation risk / claim window expiry) that cannot be undone once the contract is unpaused.

---

### Likelihood Explanation

The `PauseManager` role is a standard operational tool used during upgrades, incident response, or suspected exploits — pauses are a routine, expected event in the contract's lifecycle. The `BtcLightClient` is explicitly positioned as infrastructure for cross-chain bridges and SPV verification consumers. Any pause that overlaps with an active bridge claim or atomic swap — even a short one — triggers the loss condition for affected users. No attacker action is required; the pause itself is the trigger.

---

### Recommendation

Add an `UnrestrictedVerify` role (or reuse `Role::DAO`) as a bypass for both verification functions, mirroring the pattern already used for `run_mainchain_gc`:

```rust
// In the Role enum, add:
/// Allows calling verify_transaction_inclusion* even when the contract is paused.
UnrestrictedVerify,

// On the verification functions:
- #[pause]
+ #[pause(except(roles(Role::UnrestrictedVerify, Role::DAO)))]
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }

- #[pause]
+ #[pause(except(roles(Role::UnrestrictedVerify, Role::DAO)))]
pub fn verify_transaction_inclusion_v2(&self, ...) -> bool { ... }
```

Alternatively, since these are read-only (`&self`) functions that do not modify contract state, consider removing the `#[pause]` guard entirely. A pause is intended to halt state-mutating operations during an emergency; blocking a pure verification query provides no additional safety and only harms downstream consumers.

---

### Proof of Concept

1. A bridge contract `bridge.near` calls `verify_transaction_inclusion_v2` on `btc-light-client.near` to confirm a user's BTC deposit before minting wrapped BTC. The bridge has a 24-hour claim window.
2. A user broadcasts a BTC transaction and waits for 6 confirmations (~1 hour).
3. The `PauseManager` pauses `btc-light-client.near` for an emergency upgrade (routine operation).
4. The user calls `bridge.near` to claim their wrapped BTC. `bridge.near` issues a cross-contract call to `verify_transaction_inclusion_v2`.
5. The call panics because `#[pause]` is active with no bypass. The bridge's claim logic fails.
6. The 24-hour claim window expires while the contract remains paused.
7. The user's BTC is locked on-chain; the NEAR-side claim is permanently forfeited.

The attacker-controlled entry path is the unprivileged NEAR caller invoking the bridge's claim function, which in turn calls `verify_transaction_inclusion_v2` — a supported production entrypoint explicitly described in the contract's architecture. [6](#0-5) [1](#0-0)

### Citations

**File:** contract/src/lib.rs (L40-46)
```rust
pub enum Role {
    /// May pause and unpause features.
    PauseManager,
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
```

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```
