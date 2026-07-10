### Title
Unprivileged Caller Can Invoke `run_mainchain_gc` with Arbitrary `batch_size`, Bypassing Rate-Limit Invariant and Prematurely Pruning Headers Required for Proof Verification — (`contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a state-mutating public method protected only by the `#[pause]` macro. When the contract is not paused, any unprivileged NEAR account can call it with an arbitrarily large `batch_size`. This bypasses the deliberate rate-limit enforced by `submit_blocks`, which calls `run_mainchain_gc(num_of_headers)` to bound pruning to the number of newly submitted headers per batch. An attacker calling `run_mainchain_gc(u64::MAX)` removes all excess headers in a single transaction, advancing `mainchain_initial_blockhash` and deleting entries from `headers_pool` and `mainchain_header_to_height` — causing `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to panic for any block that was pruned.

---

### Finding Description

`submit_blocks` is protected by both `#[pause]` and `#[trusted_relayer]`: [1](#0-0) 

`run_mainchain_gc` carries only `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [2](#0-1) 

The `#[pause]` macro only blocks calls when the contract is paused; it imposes no caller identity check when the contract is live. There is no `#[trusted_relayer]` or equivalent guard.

Inside `submit_blocks`, GC is invoked with `batch_size = num_of_headers` — the count of headers in the current submission batch: [3](#0-2) 

This is a deliberate rate-limit: GC advances the pruning window by at most as many blocks as were just submitted. An external caller bypasses this entirely by passing `u64::MAX`:

```
selected_amount_to_remove = min(total_amount_to_remove, batch_size)
``` [4](#0-3) 

With `batch_size = u64::MAX`, `selected_amount_to_remove` equals `total_amount_to_remove` — every header beyond `gc_threshold` is deleted in one call. The function then:

1. Removes each pruned block from `headers_pool` and `mainchain_header_to_height` via `remove_block_header`.
2. Removes each height from `mainchain_height_to_header`.
3. Advances `mainchain_initial_blockhash` to the new oldest block. [5](#0-4) 

---

### Impact Explanation

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height` and `headers_pool`: [6](#0-5) [7](#0-6) 

If the block has been pruned by the attacker, both calls panic — `"block does not belong to the current main chain"` or `"cannot find requested transaction block"`. Any downstream contract or protocol that relies on these verification results (e.g., a Bitcoin bridge releasing funds upon proof) will have its proof permanently invalidated for that block.

The attacker does not need to corrupt PoW or forge headers. They simply call `run_mainchain_gc(u64::MAX)` to collapse the available proof window from `[initial, tip]` down to exactly `gc_threshold` blocks, removing all headers that accumulated above the threshold before the relayer's next `submit_blocks` batch would have pruned them gradually.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can submit the transaction when the contract is not paused. The attack is cheap (a single function call with no deposit), deterministic, and front-runnable. An attacker monitoring the mempool for pending `verify_transaction_inclusion` calls can prune the target block before the proof is processed. The contract is expected to be live (not paused) during normal operation, making the window permanently open.

---

### Recommendation

Add `#[trusted_relayer]` (or an equivalent role check such as `Role::DAO` or `Role::RelayerManager`) to `run_mainchain_gc`, mirroring the access control already applied to `submit_blocks`:

```rust
// Before
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }

// After
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[trusted_relayer]          // or an explicit role check
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }
```

This ensures that only authorized relayers (or DAO) can trigger header pruning, preserving the rate-limit invariant that `submit_blocks` enforces and preventing unprivileged manipulation of `mainchain_initial_blockhash` and the headers pool.

---

### Proof of Concept

1. Relayer submits blocks slowly (1 per call). After `gc_threshold + K` blocks are stored, `K` headers are eligible for GC but have not yet been pruned because each `submit_blocks(1)` call only removes 1 header.
2. A user constructs a valid Merkle proof for a transaction in block at height `tip - gc_threshold - 1` (just outside the threshold, still present in `headers_pool`).
3. Attacker calls `run_mainchain_gc(u64::MAX)` — all `K` excess headers are deleted in one transaction; `mainchain_initial_blockhash` advances by `K`.
4. User's `verify_transaction_inclusion_v2` call panics: `"cannot find requested transaction block"` because `headers_pool` no longer contains the target block hash.
5. The proof is permanently invalidated; any bridge or protocol awaiting it is blocked. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L299-313)
```rust
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

**File:** contract/src/lib.rs (L353-356)
```rust
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
