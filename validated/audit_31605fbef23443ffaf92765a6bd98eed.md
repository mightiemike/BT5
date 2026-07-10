### Title
Storage Deposits Permanently Locked After GC — No Withdrawal Mechanism (`contract/src/lib.rs`)

---

### Summary

`submit_blocks` accepts NEAR deposits to cover on-chain storage costs for new block headers. An internal GC routine (`run_mainchain_gc`) is called inside the same function and deletes old headers, freeing storage. The NEAR that was deposited in prior calls to pay for that now-freed storage accumulates in the contract's available balance permanently. No `withdraw` function exists anywhere in the contract, so neither the DAO nor any admin role can ever recover these funds.

---

### Finding Description

`submit_blocks` is marked `#[payable]` and enforces a deposit proportional to the net storage increase of the current call:

```
diff_storage_usage = env::storage_usage() - initial_storage   // after GC runs
required_deposit   = storage_byte_cost * diff_storage_usage
refund             = amount - required_deposit                 // returned to caller
``` [1](#0-0) 

GC is invoked unconditionally inside `submit_blocks` before the deposit check:

```rust
self.run_mainchain_gc(num_of_headers);
``` [2](#0-1) 

`run_mainchain_gc` removes up to `batch_size` old headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`: [3](#0-2) 

When those entries are deleted, NEAR Protocol releases the corresponding storage-staking obligation from the contract account's minimum balance. The freed NEAR moves into the contract's spendable balance. However, the contract exposes **no `withdraw` function** — confirmed by a full-text search across all `contract/src/*.rs` files returning zero matches for `withdraw` or any outbound `Promise::transfer` other than the single excess-refund path on line 192. [4](#0-3) 

The only outbound transfer is the per-call overpayment refund. NEAR freed by GC from *previous* calls has no corresponding refund path and no admin escape hatch.

---

### Impact Explanation

During the initial fill phase — before the chain reaches `gc_threshold` stored headers — every `submit_blocks` call retains a deposit. Once GC begins running steadily, those earlier deposits are freed from storage staking but remain trapped in the contract balance. The locked amount at steady state equals approximately:

```
gc_threshold × header_storage_size × storage_byte_cost
```

For the recommended `gc_threshold = 52704` (one year of Bitcoin blocks), this is a non-trivial quantity of NEAR. The DAO role has no privileged path to recover it; the `Upgradable` plugin only manages code deployment, not balance transfers. The funds are permanently inaccessible unless the contract is replaced entirely.

---

### Likelihood Explanation

This condition is triggered by normal, intended operation. GC runs inside every `submit_blocks` call once the chain exceeds `gc_threshold`. The relayer is the primary caller and pays deposits continuously. The locked-funds accumulation is therefore a certainty in any production deployment that has been running long enough to reach the GC threshold — which is the expected steady-state for all supported chains (Bitcoin, Dogecoin, Litecoin, Zcash).

---

### Recommendation

Add a privileged `withdraw` function gated to `Role::DAO` that transfers a specified amount from the contract's available balance to a designated recipient address. This mirrors the `withdraw_funds` instruction added in the referenced Solana fix. Alternatively, track the cumulative freed-storage NEAR and return it to the original depositing relayer, though the simpler DAO-controlled withdrawal is more practical given the contract's existing role model.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 10`.
2. Call `submit_blocks` 11 times, each with a deposit sufficient to cover 1 header's storage. After call 11, GC fires and removes header 1. The NEAR deposited in call 1 is freed from storage staking.
3. Repeat until steady state. Each GC cycle frees NEAR from a prior call.
4. Attempt to call any function to withdraw the accumulated balance — none exists. Inspect the contract account balance via `near state <contract_id>`; it grows monotonically above the minimum storage-staking requirement with no drain path.
5. Confirm: grep for `withdraw` or any `Promise::transfer` beyond line 192 in `contract/src/lib.rs` returns zero results. [5](#0-4) [6](#0-5)

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
