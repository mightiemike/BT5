### Title
Unprivileged `run_mainchain_gc` Permanently Removes Blocks Still Within the Confirmation Window, Causing Permanent Proof Lockout — (`contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` carries no access control beyond the pause gate. Any unprivileged NEAR account can call it at any time. When the chain holds exactly `gc_threshold + 1` blocks, calling `run_mainchain_gc(1)` removes the oldest block from both `mainchain_header_to_height` and `headers_pool`. A subsequent `verify_transaction_inclusion` call for that block panics with `"block does not belong to the current main chain"` — permanently, because the block is gone from storage forever.

---

### Finding Description

**Access control gap on `run_mainchain_gc`**

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. When the contract is not paused, there is no role check, no `#[trusted_relayer]`, and no `#[private]` guard. Any NEAR account can call it. [1](#0-0) 

Compare with `submit_blocks`, which is gated by `#[trusted_relayer]`: [2](#0-1) 

**What GC deletes**

`remove_block_header` erases the block from both the `mainchain_header_to_height` index and `headers_pool`: [3](#0-2) 

**Why `verify_transaction_inclusion` panics after GC**

The first lookup in `verify_transaction_inclusion` is against `mainchain_header_to_height`. If the block was GC'd, this lookup returns `None` and the function panics unconditionally: [4](#0-3) 

The `confirmations <= gc_threshold` guard at line 289–292 only validates the *requested* confirmation count against the threshold; it does not guarantee the target block is still in storage: [5](#0-4) 

**Exact arithmetic showing the window overlap**

Let the chain hold `gc_threshold + 1` blocks at heights `h … h + gc_threshold`.

- `amount_of_headers_we_store = gc_threshold + 1 > gc_threshold` → GC fires.
- `total_amount_to_remove = 1`; block at height `h` is removed.
- Before removal: block at height `h` has `(h + gc_threshold − h) + 1 = gc_threshold + 1` confirmations, which satisfies `confirmations = gc_threshold` (gc_threshold + 1 ≥ gc_threshold). The proof is valid.
- After removal: block at height `h` is absent from both maps. `verify_transaction_inclusion` panics. [6](#0-5) 

---

### Impact Explanation

Once a block is removed by GC it is gone permanently — there is no re-insertion path. Any caller that constructed a valid SPV proof for a transaction in the oldest retained block, and whose proof satisfies `confirmations ≤ gc_threshold`, can be permanently blocked from ever verifying that transaction through this contract. In a cross-chain bridge context this translates to permanently locked funds.

---

### Likelihood Explanation

The attack requires no special privileges and no leaked keys. Any NEAR account can call `run_mainchain_gc` at any time. The attacker only needs to:

1. Observe that the chain has grown to `gc_threshold + 1` blocks (trivially visible via `get_mainchain_size`).
2. Submit a single `run_mainchain_gc(1)` transaction before the victim's verification call is processed.

Because `verify_transaction_inclusion` is a view call (read-only, no mempool ordering), the attacker does not need to front-run a pending transaction — they simply need to ensure GC runs before the victim queries the contract. This is straightforward to time.

---

### Recommendation

Add an explicit role check to `run_mainchain_gc` so that only trusted relayers or a privileged role (e.g., `Role::DAO` or a new `Role::GCManager`) can invoke it directly. The automatic invocation inside `submit_blocks` (which is already `#[trusted_relayer]`-gated) is safe and should remain unchanged.

```rust
// Before:
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }

// After (example):
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[trusted_relayer]          // or a dedicated role check
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }
```

Alternatively, make the public entry point a view-only query and keep the mutating logic internal, callable only from `submit_blocks`.

---

### Proof of Concept

```rust
// State-machine test (unit / sandbox):
// 1. Init contract with gc_threshold = N.
// 2. Submit N+1 blocks so the chain holds exactly N+1 headers.
// 3. Record oldest_hash = mainchain_height_to_header[initial_height].
// 4. Call run_mainchain_gc(1) from an *unprivileged* account — succeeds.
// 5. Call verify_transaction_inclusion {
//        tx_block_blockhash: oldest_hash,
//        confirmations: N,   // valid: N <= gc_threshold
//        …
//    }
// 6. Assert the call panics with "block does not belong to the current main chain".
//    The block was canonically valid at step 2; it is now permanently unverifiable.
``` [7](#0-6) [8](#0-7)

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

**File:** contract/src/lib.rs (L658-662)
```rust
    /// Remove block header and meta information
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
