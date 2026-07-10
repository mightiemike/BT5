### Title
Storage Deposits Permanently Locked After GC Frees Storage — (`File: contract/src/lib.rs`)

---

### Summary

`submit_blocks()` is `#[payable]` and collects NEAR token deposits to cover on-chain storage costs. When `run_mainchain_gc()` later removes those same blocks, the freed storage deposit is never returned to the original depositors. No withdrawal function exists anywhere in the contract. NEAR tokens accumulate permanently in the contract balance as GC cycles run.

---

### Finding Description

`submit_blocks()` implements a per-call storage accounting model: [1](#0-0) 

The flow is:
1. Snapshot `initial_storage = env::storage_usage()` before any writes.
2. Insert all submitted headers into storage.
3. Call `self.run_mainchain_gc(num_of_headers)` — which **removes** old mainchain blocks from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`.
4. Compute `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)` — the **net** delta.
5. Require `amount >= required_deposit` and refund the excess.

The accounting is correct within a single call: if GC removes more bytes than were added, `diff_storage_usage` saturates to 0 and the caller pays nothing for that call. However, the NEAR tokens paid in **previous** calls — which covered the storage for the blocks now being GC'd — remain in the contract's balance with no path out.

`run_mainchain_gc()` removes blocks unconditionally: [2](#0-1) 

It frees storage but issues no refund to whoever originally paid for it. A grep across all of `contract/src/` for `withdraw`, `refund_storage`, or `storage_deposit` returns zero matches — confirming there is no withdrawal mechanism.

Fork headers submitted via `store_fork_header()` are an additional sink: they are written to `headers_pool` and paid for through the same deposit mechanism, but GC never touches them (GC only iterates `mainchain_height_to_header`): [3](#0-2) [4](#0-3) 

Fork headers therefore consume storage permanently, but that is at least consistent — the deposit is consumed for permanent storage. The mainchain GC case is the concrete locked-funds path: deposit paid → storage freed → deposit stays.

---

### Impact Explanation

NEAR tokens paid by relayers as storage deposits accumulate in the contract balance permanently. With the recommended `gc_threshold = 52704` (one year of Bitcoin blocks), the contract continuously cycles: relayers pay deposits for new blocks, GC removes old blocks, freed deposits are never returned. Over the operational lifetime of the contract, the locked balance grows monotonically. The funds are not recoverable by any caller — there is no privileged withdrawal path either.

---

### Likelihood Explanation

Certain. GC is designed to run on every `submit_blocks()` call once the chain exceeds `gc_threshold`. The relayer service submits blocks continuously. Every GC cycle that removes previously-paid-for blocks silently locks the corresponding deposit. This is not an edge case — it is the steady-state operating mode of the contract.

---

### Recommendation

Implement one of the following:

1. **Track depositors per block and refund on GC removal.** Maintain a mapping from block hash to `(depositor, amount)` and issue a `Promise::transfer` back to the depositor when `remove_block_header` is called.
2. **Adopt NEAR's standard storage management pattern.** Use a dedicated `storage_deposit` / `storage_withdraw` interface (as used by NEP-141 tokens) that decouples storage staking from block submission, allowing depositors to reclaim freed storage at any time.
3. **At minimum, add a privileged `withdraw_freed_storage` function** that allows the DAO role to sweep excess contract balance back to a treasury, with clear documentation that individual depositors are not refunded.

---

### Proof of Concept

```
// Round 1: relayer pays for 100 blocks
submit_blocks(headers_1_to_100, deposit = 100 * storage_byte_cost * header_size)
// → 100 blocks stored, deposit consumed, no refund

// Round 2: relayer submits 100 more blocks; GC removes headers_1_to_100
submit_blocks(headers_101_to_200, deposit = 100 * storage_byte_cost * header_size)
// → net storage delta = +100 new - 100 GC'd = 0
// → required_deposit = 0, full deposit refunded to Round 2 caller
// → BUT Round 1's deposit is still sitting in the contract balance
// → No function exists to retrieve it

// After N GC cycles: contract balance = N * (100 * storage_byte_cost * header_size)
// permanently locked, no withdrawal path
``` [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L377-416)
```rust
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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```
