### Title
Storage Deposit Accumulation With No Withdrawal Path After GC Frees Storage — (`contract/src/lib.rs`)

---

### Summary

`submit_blocks` collects NEAR storage deposits from relayers to pay for on-chain header storage. When the garbage collector (`run_mainchain_gc`) later removes those headers, the storage staking requirement decreases and the previously locked NEAR is released back into the contract's available balance. There is no withdrawal function in the contract to recover this freed balance. Over the operational lifetime of the contract, NEAR tokens accumulate permanently in the contract account with no recovery path.

---

### Finding Description

`submit_blocks` is `#[payable]` and measures net storage growth after both header insertion and GC: [1](#0-0) 

The critical sequence is:

1. `initial_storage = env::storage_usage()` is captured before any work.
2. New headers are inserted (storage grows).
3. `run_mainchain_gc(num_of_headers)` removes old mainchain blocks (storage shrinks).
4. `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)` — the **net** delta.
5. `required_deposit` is computed from the net delta; excess is refunded to the caller. [2](#0-1) 

The refund logic is correct for the **current** call. The problem is the **historical** deposits. Every previous `submit_blocks` call paid for storage that is now being freed by GC. In NEAR Protocol, when storage is freed the protocol releases the corresponding locked balance back into the contract's available balance — but it does not refund the original depositor. Those NEAR tokens now sit in the contract's account with no owner and no withdrawal path.

`run_mainchain_gc` removes headers from both `mainchain_height_to_header` and `headers_pool`, freeing storage bytes: [3](#0-2) 

The contract exposes no `withdraw`, `claim`, or admin-drain function. The full public API is `submit_blocks`, `run_mainchain_gc`, read-only getters, and `verify_transaction_inclusion*`. None transfer the contract's available balance to any external account. [4](#0-3) 

---

### Impact Explanation

NEAR tokens paid as storage deposits by relayers become permanently locked in the contract's account balance as GC continuously frees storage. With the recommended `gc_threshold = 52704` (approximately one year of Bitcoin blocks), the contract reaches a steady state where it stores ~52704 headers at any given time, but the cumulative deposits paid for all previously GC'd headers — potentially millions of blocks over the contract's lifetime — remain in the contract with no recovery mechanism. The only escape is a privileged contract upgrade that adds a withdrawal function, which is an out-of-band privileged action not available to the depositing relayers.

---

### Likelihood Explanation

This is triggered by normal, intended operation. GC runs automatically on every `submit_blocks` call once the mainchain exceeds `gc_threshold`. Any relayer operating the system for an extended period will continuously contribute to the accumulating stuck balance. No adversarial action is required; the vulnerability is inherent to the design. [5](#0-4) 

---

### Recommendation

Add an admin-only withdrawal function that allows the contract owner or DAO to recover the contract's available (non-storage-staked) balance:

```rust
pub fn withdraw_available_balance(&mut self, receiver_id: AccountId) -> Promise {
    self.assert_role(Role::DAO); // or equivalent admin check
    let available = env::account_balance()
        .saturating_sub(env::storage_byte_cost()
            .saturating_mul(env::storage_usage().into()));
    Promise::new(receiver_id).transfer(available)
}
```

Alternatively, track per-relayer deposits and issue refunds when GC frees the storage they originally paid for.

---

### Proof of Concept

1. Relayer submits 100 headers with a deposit of 1 NEAR (covering storage cost). Contract stores 100 headers; 1 NEAR is locked as storage staking.
2. Relayer submits 100 more headers. GC removes the first 100 headers. Net storage delta = 0 (100 added, 100 removed). `required_deposit = 0`. The full new deposit is refunded to the relayer.
3. The original 1 NEAR from step 1 is now released from storage staking into the contract's available balance — but there is no function to withdraw it.
4. This cycle repeats indefinitely. After N batches of 100 headers each, approximately N NEAR tokens are permanently locked in the contract's available balance. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L169-198)
```rust
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

**File:** contract/CLAUDE.md (L34-40)
```markdown
### On-Chain State (`BtcLightClient` in `contract/src/lib.rs`)

- **headers_pool**: `LookupMap<H256, ExtendedHeader>` — all stored headers (main chain + forks)
- **mainchain_height_to_header** / **mainchain_header_to_height**: bidirectional main chain index
- **mainchain_tip_blockhash**: current chain tip
- **gc_threshold**: max number of mainchain blocks to keep in storage. When the mainchain grows beyond this, the oldest mainchain blocks are pruned. GC runs automatically during `submit_blocks()` (with `batch_size` = number of submitted headers) and can also be triggered manually via `run_mainchain_gc(batch_size)`. Only mainchain blocks are deleted; fork/sidechain blocks are not affected

```
