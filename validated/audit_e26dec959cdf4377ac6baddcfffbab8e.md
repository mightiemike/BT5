### Title
Paused Protocol Blocks Critical SPV Proof Verification, Causing Irreversible Loss for Time-Sensitive Bridge Operations - (File: contract/src/lib.rs)

### Summary
The `BtcLightClient` contract applies the `#[pause]` macro to `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` without any bypass role exception. When the contract is paused, any downstream bridge or cross-chain application that depends on these functions for time-sensitive claim windows is permanently blocked from completing verification. If the pause outlasts a bridge's claim deadline, users suffer irreversible loss of funds with no recourse.

### Finding Description
The contract uses `near_plugins`' `Pausable` trait and applies `#[pause]` to four functions. Two of them — `run_mainchain_gc` and `submit_blocks` — were given explicit bypass roles (`UnrestrictedRunGC` and `UnrestrictedSubmitBlocks` respectively), demonstrating the developers understood that some functions must remain accessible during a pause. However, the two SPV proof verification functions received no such bypass:

- `verify_transaction_inclusion` at line 287: `#[pause]` with no `except` clause
- `verify_transaction_inclusion_v2` at line 346: `#[pause]` with no `except` clause

`verify_transaction_inclusion_v2` also internally delegates to `verify_transaction_inclusion` (line 368), so both are fully blocked under a pause.

These are the only public API functions that downstream bridge contracts call to confirm Bitcoin transaction inclusion on NEAR. Cross-chain bridges universally implement time-bounded claim windows (e.g., 3–7 days) after which a pending transfer expires and the user's locked funds are forfeited. If the light client is paused during such a window, the bridge consumer cannot call `verify_transaction_inclusion_v2`, the claim cannot be finalized, and when the pause lifts the window may already be expired.

The asymmetry is stark: `submit_blocks` (the write path) has a privileged bypass role so trusted relayers can keep the chain state current even during a pause, but `verify_transaction_inclusion_v2` (the read path that consumers depend on) has no bypass at all.

### Impact Explanation
High. Any user with a pending cross-chain transfer whose claim window overlaps with a pause period faces guaranteed, irreversible loss of funds. The verification functions are pure read operations (`&self`) that only inspect already-committed, already-validated state — there is no security rationale for blocking them during a pause. The broken invariant is: a valid Bitcoin SPV proof that was provable before the pause remains provable after the pause, but the pause window silently consumes the bridge's claim deadline, making the proof permanently unclaimable.

### Likelihood Explanation
Low. Protocol pauses are rare emergency events. However, the severity when a pause does occur is maximal for any user whose bridge claim window is active at that moment, and the loss is unrecoverable once the deadline passes.

### Recommendation
Add a bypass role for the verification functions, mirroring the pattern already used for `run_mainchain_gc`:

```diff
- #[pause]
+ #[pause(except(roles(Role::UnrestrictedVerify)))]
  pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }

- #[pause]
+ #[pause(except(roles(Role::UnrestrictedVerify)))]
  pub fn verify_transaction_inclusion_v2(&self, ...) -> bool { ... }
```

Alternatively, since both functions are pure read operations that do not modify state and only access already-validated headers, remove `#[pause]` from them entirely. The pause mechanism is intended to halt state-mutating operations during emergencies; blocking read-only proof verification provides no additional safety and only harms users.

### Proof of Concept
Scenario demonstrating guaranteed loss of a cross-chain transfer:

1. User locks BTC on Bitcoin and initiates a cross-chain transfer on a NEAR bridge that uses `BtcLightClient` for SPV verification.
2. The bridge contract enforces a 3-day claim window starting from the Bitcoin transaction's confirmation block.
3. After 1 day, the `PauseManager` pauses `BtcLightClient` due to a suspected anomaly in `submit_blocks`.
4. The user attempts to call `verify_transaction_inclusion_v2` through the bridge — the call panics because `#[pause]` blocks it unconditionally (no bypass role exists).
5. The pause lasts 3 days.
6. When the contract unpauses, the bridge's 3-day claim window has expired.
7. The bridge contract rejects the claim as expired; the user's locked BTC is permanently forfeited.
8. The user had a fully valid SPV proof throughout — the only barrier was the pause on the read-only verification function. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L43-46)
```rust
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

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```
