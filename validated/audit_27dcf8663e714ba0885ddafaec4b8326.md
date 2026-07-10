### Title
Unprivileged Caller Can Aggressively Prune Mainchain State via `run_mainchain_gc` — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating NEAR contract method protected only by a pause guard. When the contract is not paused (its normal operating state), any unprivileged NEAR account can call it with an arbitrarily large `batch_size`, immediately pruning all mainchain block headers that exceed `gc_threshold` in a single transaction. This permanently invalidates pending SPV proofs for those blocks and can prevent legitimate chain reorganizations from completing.

---

### Finding Description

`run_mainchain_gc` carries only `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

The `#[pause]` macro from `near_plugins` restricts callers **only when the contract is paused**. When the contract is in its normal, unpaused operating state, the macro imposes no caller restriction whatsoever — any NEAR account can invoke the function. There is no `#[trusted_relayer]`, no role check, and no `#[private]` guard.

Compare this with `submit_blocks`, which carries both `#[pause]` and `#[trusted_relayer]`: [2](#0-1) 

The intended design is that GC runs automatically inside `submit_blocks` with `batch_size = num_of_headers` — a small, bounded number per call — gradually pruning old blocks over time: [3](#0-2) 

An attacker bypasses this gradual design by calling `run_mainchain_gc(u64::MAX)` directly. The function computes `total_amount_to_remove = amount_of_headers_we_store - gc_threshold` and then `selected_amount_to_remove = min(total_amount_to_remove, batch_size)`: [4](#0-3) 

With `batch_size = u64::MAX`, `selected_amount_to_remove` equals `total_amount_to_remove`, so **all** blocks beyond `gc_threshold` are removed in one call. The removal deletes entries from `headers_pool`, `mainchain_header_to_height`, and `mainchain_header_to_height` (via `remove_block_header`): [5](#0-4) 

After pruning, `verify_transaction_inclusion` looks up the pruned block hash in `mainchain_header_to_height` and panics with `"block does not belong to the current main chain"`: [6](#0-5) 

This is a permanent, irreversible failure for any downstream consumer that held a valid proof for a block that was aggressively GC'd.

---

### Impact Explanation

1. **Permanent SPV proof invalidation**: Any downstream contract or user waiting to call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction in a block that is beyond `gc_threshold` but not yet naturally GC'd will have their proof permanently destroyed. The block hash is removed from `mainchain_header_to_height` and `headers_pool`, making the proof call panic unconditionally. For bridge contracts or settlement systems that rely on SPV proofs to release funds, this constitutes a permanent denial of the proof result — effectively freezing or losing the associated value.

2. **Reorg prevention**: The CLAUDE.md documents that reorgs fail if mainchain blocks near the fork point have been GC'd: [7](#0-6) 

An attacker can trigger aggressive GC immediately before or during a legitimate reorg submission, causing the reorg to panic with `PrevBlockNotFound` and leaving an incorrect chain as the canonical main chain.

---

### Likelihood Explanation

The entry path requires no preconditions: any NEAR account with gas can call `run_mainchain_gc` on the unpaused contract. The function is documented as a "Public call" and is reachable from the NEAR RPC without any staking, role, or deposit requirement. The attack is cheap (one NEAR transaction) and repeatable.

---

### Recommendation

Add caller authorization to `run_mainchain_gc` equivalent to what `submit_blocks` uses. Either apply `#[trusted_relayer]` to restrict it to registered relayers, or introduce a dedicated role check (e.g., `Role::UnrestrictedRunGC` should gate the function when unpaused, not only when paused). The `#[pause(except(roles(...)))]` pattern only controls paused-state access and provides no protection during normal operation.

---

### Proof of Concept

1. Contract is deployed with `gc_threshold = 52704` (one year of Bitcoin blocks).
2. The relayer submits blocks over time; `submit_blocks` calls `run_mainchain_gc(N)` with small `N` per batch, so blocks beyond the threshold accumulate (e.g., 500 blocks beyond threshold remain un-GC'd).
3. A downstream bridge contract has a pending `verify_transaction_inclusion` call for a transaction in one of those 500 blocks.
4. Attacker calls `run_mainchain_gc(u64::MAX)` from any NEAR account — no role, no deposit required.
5. All 500 blocks beyond `gc_threshold` are immediately removed from `headers_pool` and `mainchain_header_to_height`.
6. The bridge contract's `verify_transaction_inclusion` call now panics with `"block does not belong to the current main chain"` — permanently, since the block data is gone.
7. The bridge cannot release the user's funds; the value is frozen.

### Citations

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

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

**File:** contract/src/lib.rs (L401-414)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```
