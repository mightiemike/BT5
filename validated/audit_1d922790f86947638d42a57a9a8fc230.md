### Title
No Method to Withdraw Accumulated NEAR Tokens Freed by GC — (File: `contract/src/lib.rs`)

---

### Summary

The `BtcLightClient` contract accepts NEAR token deposits from relayers to cover on-chain storage costs when submitting block headers. As the garbage-collection mechanism (`run_mainchain_gc`) removes old headers and frees storage, the NEAR tokens previously deposited for that storage accumulate in the contract's balance permanently. There is no admin or DAO function to withdraw these tokens.

---

### Finding Description

`submit_blocks` is marked `#[payable]` and charges the caller for the **net** storage increase within a single transaction: [1](#0-0) 

The accounting works as follows:

1. `initial_storage` is snapshotted before any writes.
2. New headers are inserted and `run_mainchain_gc` is called.
3. `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)` captures the **net** delta.
4. Only the net delta is charged; the excess deposit is refunded to `predecessor_account_id`.

The critical gap is that this refund only covers the **current call's** excess. When GC removes headers that were paid for in **earlier** calls, the NEAR tokens from those earlier deposits are not returned to anyone — they remain in the contract's account balance.

`run_mainchain_gc` is also a standalone public method callable by any account (no `#[trusted_relayer]` guard, only a `#[pause]` decorator): [2](#0-1) 

It removes entries from `mainchain_height_to_header`, `mainchain_header_to_height`, and `headers_pool`, freeing storage bytes, but transfers nothing: [3](#0-2) 

No function in the contract — including DAO-gated or admin-gated paths — provides a mechanism to withdraw the contract's accumulated NEAR balance.

---

### Impact Explanation

Every GC cycle converts previously deposited NEAR (paid for storage that is now freed) into permanently locked contract balance. With the recommended `gc_threshold` of `52704` blocks (~1 year of Bitcoin blocks), the contract continuously cycles: relayers deposit NEAR for new headers, GC frees old headers, and the deposited NEAR for freed storage is never returned. Over the operational lifetime of the contract, this results in a growing, irrecoverable NEAR balance with no withdrawal path for the DAO or any admin role.

---

### Likelihood Explanation

This is certain under normal operation. GC is triggered automatically inside every `submit_blocks` call via `self.run_mainchain_gc(num_of_headers)` once the stored chain exceeds `gc_threshold`. It is also callable by any unprivileged account directly. Every GC cycle that removes headers paid for in prior calls locks those deposits permanently.

---

### Recommendation

Add a DAO-only (or admin-only) function to withdraw the contract's excess NEAR balance, analogous to the `transferGas(...)` pattern referenced in the original report. For example:

```rust
pub fn withdraw_near(&mut self, amount: NearToken, recipient: AccountId) {
    // restrict to Role::DAO
    self.acl_assert_role(Role::DAO, &env::predecessor_account_id());
    Promise::new(recipient).transfer(amount);
}
```

This ensures that NEAR freed by GC cycles can be recovered by the protocol's governance rather than being permanently locked.

---

### Proof of Concept

1. Relayer A calls `submit_blocks` with 85 headers and attaches deposit `D` covering storage for 85 headers. The deposit is accepted; `D` enters the contract balance.
2. The chain grows past `gc_threshold`. On the next `submit_blocks` call (or a direct call to `run_mainchain_gc`), 85 old headers are removed, freeing the storage bytes that `D` paid for.
3. The current call's net storage delta is reduced (or zero via `saturating_sub`), so the current caller pays little or nothing — but Relayer A's `D` NEAR is not returned.
4. Repeat over months of operation: the contract accumulates NEAR proportional to the total storage ever freed by GC, with no withdrawal path. [4](#0-3) [5](#0-4)

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
