### Title
Unprivileged Caller Can Aggressively Drain Verifiable Block History via `run_mainchain_gc` — (`contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function protected only by a pause-gate. When the contract is live (unpaused), any NEAR account — with no role, stake, or privilege — can call it with `batch_size = u64::MAX` and immediately prune every block eligible for GC in a single transaction. Because `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` hard-panic when the target block has been removed from `mainchain_header_to_height`, an attacker can front-run any pending verification call and cause it to revert permanently, bricking integrating contracts.

---

### Finding Description

`run_mainchain_gc` is declared `pub` and carries only `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

The `#[pause]` macro gates the call only when the contract is administratively paused. While unpaused — the normal production state — there is no `acl_role`, `#[private]`, or any other caller restriction. The `batch_size` parameter is documented as a gas-rate-limiter, but nothing enforces who supplies it or what value they choose.

Inside the function, `selected_amount_to_remove` is capped at `total_amount_to_remove = amount_of_headers_we_store - gc_threshold`: [2](#0-1) 

An attacker passes `u64::MAX`; `std::cmp::min` selects `total_amount_to_remove`, so every block beyond the threshold is removed in one call. The loop calls `remove_block_header` for each height: [3](#0-2) 

`remove_block_header` deletes the entry from both `mainchain_header_to_height` and `headers_pool`: [4](#0-3) 

After the loop, `mainchain_initial_blockhash` is advanced to the new oldest block: [5](#0-4) 

Any subsequent call to `verify_transaction_inclusion` (or `_v2`) for a block that was just pruned hard-panics at: [6](#0-5) 

because `mainchain_header_to_height` no longer contains the hash. The transaction reverts with `"block does not belong to the current main chain"`.

---

### Impact Explanation

An attacker can permanently invalidate any pending SPV proof for a transaction whose containing block sits in the GC-eligible window (i.e., older than `gc_threshold` blocks from the current tip but not yet pruned by the relayer). Integrating contracts — bridges, escrows, or settlement layers — that call `verify_transaction_inclusion_v2` after waiting for confirmations will receive a hard revert instead of a boolean result. Because the block is gone from storage, no retry is possible; the verification is permanently bricked for that block. The corrupted canonical mapping is `mainchain_header_to_height` and `headers_pool`, and the broken invariant is: *every block within the GC-eligible window must remain available until the relayer's own GC pass removes it*.

---

### Likelihood Explanation

High. The contract is unpaused during normal operation. The call requires no deposit, no role, and no special knowledge beyond the contract's ABI. A single NEAR transaction suffices. The attacker does not need to know which specific block a victim is about to verify — calling `run_mainchain_gc(u64::MAX)` blindly removes the entire eligible window, covering any block the victim might target.

---

### Recommendation

Restrict `run_mainchain_gc` to trusted callers by adding an access-control role check, mirroring the pattern already used for `submit_blocks`:

```rust
// Option A: restrict to a dedicated role
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[access_control_any(roles(Role::DAO, Role::RelayerManager))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }
```

Alternatively, make the function `pub(crate)` or remove the external entry point entirely, since `submit_blocks` already calls it internally with a safe batch size. If the external call is kept for operational convenience, `lastUpdateBlock`-style rate-limiting alone is insufficient; a role check is required.

---

### Proof of Concept

1. Contract is deployed with `gc_threshold = 52_704`. The relayer has submitted 60_000 blocks; `amount_of_headers_we_store = 60_000`, so `total_amount_to_remove = 7_296`.
2. An integrating bridge contract is about to call `verify_transaction_inclusion_v2` for a Bitcoin transaction confirmed in block at height `initial_height + 5_000` (within the eligible window).
3. Attacker submits a NEAR transaction: `btc_light_client.run_mainchain_gc({"batch_size": 18446744073709551615})`.
4. The contract removes all 7_296 eligible blocks. `mainchain_initial_blockhash` advances past height `initial_height + 7_296`. The target block's hash is deleted from `mainchain_header_to_height` and `headers_pool`.
5. The bridge contract's `verify_transaction_inclusion_v2` call executes and panics at line 300–301 with `"block does not belong to the current main chain"`. The bridge's cross-chain settlement is permanently bricked for that transaction.

### Citations

**File:** contract/src/lib.rs (L299-301)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L391-393)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L401-408)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
```

**File:** contract/src/lib.rs (L411-414)
```rust
            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```

**File:** contract/src/lib.rs (L659-661)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
```
