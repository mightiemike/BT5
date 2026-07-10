### Title
Unprivileged Caller Can Trigger `run_mainchain_gc` to Prune All Eligible Historical Blocks — (`contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public NEAR contract method protected only by a pause gate. When the contract is not paused, **any** NEAR account can call it with an arbitrarily large `batch_size`, immediately pruning all mainchain blocks beyond `gc_threshold` in a single transaction. This advances `mainchain_initial_blockhash` to `tip - gc_threshold`, causing all `verify_transaction_inclusion` calls for pruned blocks to panic with `"block does not belong to the current main chain"`.

---

### Finding Description

The method is declared as:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [1](#0-0) 

The `#[pause(except(roles(...)))]` macro from `near-plugins` only restricts callers **when the contract is paused**. When the contract is **not** paused, the decorator is a no-op and any NEAR account can call the method freely. There is no `#[trusted_relayer]` guard, no `#[private]` restriction, and no explicit `predecessor_account_id` check.

Compare with `submit_blocks`, which carries **both** `#[pause]` and `#[trusted_relayer]`:

```rust
#[payable]
#[pause]
#[trusted_relayer]
pub fn submit_blocks(...)
``` [2](#0-1) 

`run_mainchain_gc` has no equivalent relayer gate.

Inside the function, the removal logic is:

```rust
let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
``` [3](#0-2) 

An attacker passing `batch_size >= total_amount_to_remove` (e.g., `u64::MAX`) causes the contract to remove every block beyond `gc_threshold` in one call, then updates `mainchain_initial_blockhash` to the new oldest block:

```rust
self.mainchain_initial_blockhash = self
    .mainchain_height_to_header
    .get(&end_removal_height)
    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
``` [4](#0-3) 

After this, `verify_transaction_inclusion` for any pruned block panics at:

```rust
.unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [5](#0-4) 

because `mainchain_header_to_height` no longer contains the pruned block hashes (they are removed in `remove_block_header`). [6](#0-5) 

---

### Impact Explanation

An attacker can, at any time while the contract is unpaused, call `run_mainchain_gc(u64::MAX)` to immediately prune every block older than `gc_threshold` from the canonical chain index. This:

- Invalidates all in-flight SPV proofs for blocks that were eligible for GC but had not yet been pruned by the relayer.
- Advances `mainchain_initial_blockhash` to `tip - gc_threshold` in one transaction, making the light client behave as if all older history never existed.
- Can be repeated every time the relayer submits enough blocks to push the chain beyond `gc_threshold` again, creating a persistent griefing loop.

The attacker does not need any role, stake, or deposit beyond standard NEAR gas fees.

---

### Likelihood Explanation

The attack path is trivially reachable: one NEAR transaction from any account, no preconditions beyond the contract being unpaused (its normal operating state). The `run_mainchain_gc` method is documented as a "Public call" in its own doc comment, confirming the exposure is not accidental. [7](#0-6) 

---

### Recommendation

Add `#[trusted_relayer]` (or an equivalent ACL check such as `Role::DAO` / `Role::RelayerManager`) to `run_mainchain_gc`, mirroring the guard already present on `submit_blocks`. The `Role::UnrestrictedRunGC` role should remain as the pause-bypass role, but the base (unpaused) path must also require a privileged caller.

---

### Proof of Concept

```rust
// In a near-sdk-sim or workspaces-rs test:
// 1. Initialize contract with gc_threshold = 52704, submit > 52704 blocks.
// 2. Call from an unprivileged account:
unprivileged_account
    .call(contract_id, "run_mainchain_gc")
    .args_json(json!({ "batch_size": u64::MAX }))
    .transact()
    .await?;
// 3. Assert mainchain_initial_blockhash advanced by (stored_count - gc_threshold).
// 4. Call verify_transaction_inclusion for any pruned block hash.
// 5. Observe panic: "block does not belong to the current main chain".
```

The exploit requires no privileged keys, no DAO role, and no relayer compromise — only a standard NEAR account and gas.

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L300-301)
```rust
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L371-372)
```rust
    /// Public call to run GC on a mainchain.
    /// `batch_size` is how many block headers should be removed in the execution
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L392-393)
```rust
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
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
