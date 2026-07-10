### Title
Storage Deposit NEAR Permanently Locked After GC Frees Storage — No Withdrawal Function Exists - (`contract/src/lib.rs`)

### Summary

`submit_blocks` collects NEAR storage deposits from callers and retains the exact amount consumed by new storage. When `run_mainchain_gc` later deletes those same block headers and frees the storage, the corresponding NEAR tokens remain permanently locked inside the contract. No `withdraw`, `sweep`, or recovery function exists anywhere in the contract, making the freed deposits irrecoverable.

### Finding Description

`submit_blocks` is `#[payable]` and computes a `required_deposit` equal to `env::storage_byte_cost() * diff_storage_usage`. Only the surplus above that amount is refunded to the caller; the exact cost is retained by the contract. [1](#0-0) 

`run_mainchain_gc` (called automatically inside `submit_blocks` and also callable directly) removes old block headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`, freeing the on-chain storage those entries occupied. [2](#0-1) 

When storage is freed on NEAR, the protocol does not automatically return the deposit to anyone — the contract account simply retains the yoctoNEAR that was paid for that storage. Because the contract exposes no `withdraw`, `sweep`, or equivalent function, those freed deposits accumulate indefinitely and are permanently inaccessible.

A grep across all contract source files for `withdraw`, `sweep`, `storage_release`, and `transfer` returns only the two lines inside `submit_blocks` that refund the *excess* deposit to the caller — confirming there is no recovery path for the *retained* portion. [3](#0-2) 

### Impact Explanation

Every operational cycle of the contract deposits NEAR for new headers and then GC's old ones. The NEAR paid for GC'd headers is permanently locked. With the recommended `gc_threshold` of 52,704 blocks (~1 year of Bitcoin blocks) and a continuous relay, the locked balance grows monotonically. The funds cannot be recovered without a contract upgrade, and no upgrade path for this specific issue is provided.

### Likelihood Explanation

This is triggered by normal, intended operation. The relayer submits blocks continuously; GC runs automatically inside every `submit_blocks` call once the threshold is exceeded. No adversarial action is required — the loss occurs on every GC cycle as a structural consequence of the missing withdrawal function.

### Recommendation

Add a privileged (DAO-gated) withdrawal function that transfers the contract's free balance (i.e., `env::account_balance() - env::storage_byte_cost() * env::storage_usage()`) to a designated recipient, analogous to the `spend` method recommended in the original report:

```rust
pub fn withdraw_freed_storage_deposit(
    &mut self,
    recipient: AccountId,
    amount: NearToken,
) {
    // require DAO role
    let available = env::account_balance()
        .saturating_sub(env::storage_byte_cost()
            .saturating_mul(env::storage_usage().into()));
    require!(amount <= available, "Insufficient free balance");
    Promise::new(recipient).transfer(amount);
}
```

### Proof of Concept

1. Relayer calls `submit_blocks` with N headers, attaching `N * storage_byte_cost * bytes_per_header` NEAR. The contract retains the exact storage cost.
2. After enough blocks accumulate, `run_mainchain_gc` (called inside the same `submit_blocks`) removes the oldest headers, freeing their storage bytes.
3. The freed NEAR remains in the contract's account balance.
4. No function in `contract/src/lib.rs` (or any other contract source file) allows anyone — including the DAO — to transfer that balance out.
5. Repeat indefinitely: every GC cycle locks more NEAR with no recovery path. [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L173-197)
```rust
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
