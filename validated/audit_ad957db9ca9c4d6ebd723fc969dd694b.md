### Title
Storage-Deposit NEAR Tokens Permanently Locked After Garbage Collection — (`contract/src/lib.rs`)

### Summary
`submit_blocks` collects NEAR token deposits to cover on-chain storage costs for new block headers. When `run_mainchain_gc` later deletes those headers, the corresponding storage is freed and the previously-locked NEAR tokens are returned to the contract's own account balance. No function exists to withdraw these accumulated tokens, so they are permanently locked in the contract with no recovery path.

### Finding Description

`submit_blocks` is `#[payable]` and requires callers to attach enough NEAR to cover the net storage increase: [1](#0-0) 

The function measures `initial_storage`, submits headers (adding storage), runs GC (which may delete older headers), then computes `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)`. Any deposit above `required_deposit` is refunded to `predecessor_account_id`. The deposit that exactly covers the net new storage is retained in the contract.

`run_mainchain_gc` deletes mainchain headers from all three maps: [2](#0-1) 

On NEAR Protocol, deleting storage entries reduces the contract's `storage_usage`, which in turn reduces the minimum balance the contract must hold for storage staking. The previously-locked NEAR tokens are released back into the contract's spendable balance. However, there is no `withdraw`, `sweep`, or admin-transfer function anywhere in the contract to move these freed tokens out. They accumulate silently in the contract account.

Additionally, `store_fork_header` stores fork/sidechain headers in `headers_pool` but GC only prunes mainchain blocks: [3](#0-2) 

Fork headers are never deleted, so their storage deposits are also permanently locked.

### Impact Explanation

Over the operational lifetime of the contract (Bitcoin produces ~52,704 blocks/year; `gc_threshold` is recommended at 52,704), the GC cycle continuously frees storage that was paid for by relayer deposits. Each freed block header releases approximately `storage_byte_cost × sizeof(ExtendedHeader)` yoctoNEAR back into the contract balance. At scale, this amounts to a non-trivial quantity of NEAR tokens that no party — not the relayer who paid, not the DAO, not any admin — can ever retrieve. The funds are permanently inaccessible.

### Likelihood Explanation

This is not a theoretical edge case. It is the normal, steady-state operation of the contract: relayers deposit NEAR to submit blocks, GC runs automatically inside every `submit_blocks` call, and freed storage deposits accumulate. The longer the contract runs, the larger the locked balance grows. No special attacker action is required; the protocol's own design causes the accumulation.

### Recommendation

Track the cumulative freed storage balance and expose a DAO/admin-gated `withdraw_freed_storage(amount)` function, or refund freed storage to the original depositor. At minimum, add a DAO-only function that can transfer the contract's surplus balance (balance above minimum storage requirement) to a designated treasury address, consistent with the proxy-upgrade escape hatch already acknowledged for similar situations in the reference report.

### Proof of Concept

1. Relayer calls `submit_blocks` with 100 headers and attaches `100 × storage_byte_cost × sizeof(ExtendedHeader)` yoctoNEAR.
2. Contract stores 100 headers; `run_mainchain_gc` deletes 100 old headers (net storage change = 0, so `diff_storage_usage = 0`, `required_deposit = 0`).
3. The full deposit is refunded to the relayer — but the NEAR tokens that were originally deposited in *prior* calls to cover those now-deleted headers remain in the contract balance.
4. Repeat for every `submit_blocks` call once the chain exceeds `gc_threshold`. Each cycle frees storage whose original deposit is irrecoverable.
5. Confirm: `grep` the entire contract for any `Promise::new(...).transfer(...)` or `env::account_balance()` usage outside of the single refund path in `submit_blocks` — none exists. [4](#0-3) [5](#0-4)

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

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```
