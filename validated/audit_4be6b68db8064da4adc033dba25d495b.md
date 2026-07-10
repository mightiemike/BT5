### Title
Permissionless `run_mainchain_gc` Allows Any Caller to Front-Run and Permanently Invalidate SPV Proofs - (File: contract/src/lib.rs)

---

### Summary

`run_mainchain_gc` carries no role-based access control when the contract is unpaused. Any unprivileged NEAR account can call it with an attacker-chosen `batch_size`, removing mainchain block headers from on-chain storage. Because `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` require the target block to be present in `mainchain_header_to_height`, an attacker can front-run a user's SPV verification call, delete the target block via GC, and cause the verification to panic — permanently breaking the proof for that block.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

When the contract is not paused, this attribute imposes **no caller restriction**. Any NEAR account can invoke it. The function accepts a caller-supplied `batch_size` with no upper-bound validation: [2](#0-1) 

Passing `u64::MAX` causes the function to remove every block that is currently eligible for GC (all blocks beyond `gc_threshold` from the tip) in a single call. The removal deletes entries from both `mainchain_header_to_height` and `headers_pool` via `remove_block_header`: [3](#0-2) 

`verify_transaction_inclusion` then hard-panics if the target block is absent from `mainchain_header_to_height`: [4](#0-3) 

The guard `args.confirmations <= self.gc_threshold` does **not** guarantee the block is still in storage — it only bounds the requested confirmation count. A block can have far more than `gc_threshold` confirmations (making it GC-eligible) while the user legitimately requests only a small number of confirmations (e.g., 6). The check passes, but the block has already been erased.

`verify_transaction_inclusion_v2` is equally affected because it delegates to `verify_transaction_inclusion`: [5](#0-4) 

By contrast, `submit_blocks` — the only other caller of `run_mainchain_gc` — is gated by `#[trusted_relayer]`, which rejects any account without the `UnrestrictedSubmitBlocks` role: [6](#0-5) 

The asymmetry is the root cause: the internal GC path is privileged, but the public GC path is not.

---

### Impact Explanation

An attacker can permanently prevent SPV verification for any transaction whose containing block is GC-eligible. Any downstream NEAR contract (e.g., a cross-chain bridge, a payment channel, a custody protocol) that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to release funds or confirm a Bitcoin event will receive a panic instead of a result. Because the block is deleted from storage, the proof cannot be retried — the state is irreversibly corrupted for that block. Users whose transactions are in GC-eligible blocks lose the ability to prove inclusion on-chain.

---

### Likelihood Explanation

The attack is realistic under normal operating conditions:

- Bitcoin produces ~52,704 blocks per year; the recommended `gc_threshold` is exactly 52,704. After one year of operation the chain routinely exceeds the threshold, making the oldest blocks GC-eligible.
- NEAR transactions are visible before finalization. An attacker monitoring the mempool can observe a pending `verify_transaction_inclusion` call, extract the target block hash, and submit `run_mainchain_gc(u64::MAX)` with higher priority to execute first within the same block.
- The call costs only gas — no stake, no role, no deposit required.
- The attack can be repeated indefinitely to block any future verification attempt for the same block.

---

### Recommendation

Add a role check to `run_mainchain_gc` so that only privileged accounts (e.g., `Role::DAO` or a dedicated `GCManager` role) can invoke it directly:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[access_control_any(roles(Role::DAO, Role::UnrestrictedRunGC))]  // add this
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }
```

Alternatively, make `run_mainchain_gc` `pub(crate)` so it is only reachable through the privileged `submit_blocks` path, removing the public entry point entirely.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704`.
2. The relayer submits blocks until the chain reaches height 52705 (one block beyond the threshold). Block at height 0 is now GC-eligible.
3. A user constructs a `verify_transaction_inclusion` call for a transaction in the block at height 0, requesting 6 confirmations. The call `6 <= 52704` passes the guard.
4. Before the user's transaction is finalized, the attacker calls:
   ```
   run_mainchain_gc(18446744073709551615)  // u64::MAX
   ```
   This removes the block at height 0 from `mainchain_height_to_header` and `headers_pool`.
5. The user's `verify_transaction_inclusion` call executes and panics:
   ```
   "block does not belong to the current main chain"
   ```
6. The block is gone from storage permanently. No retry is possible. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L288-313)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** contract/src/lib.rs (L376-416)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

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
        }
    }
```
