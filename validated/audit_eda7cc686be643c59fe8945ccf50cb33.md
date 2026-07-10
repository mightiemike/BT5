### Title
NEAR Storage Deposits Accumulate Permanently With No Withdrawal Path — (`contract/src/lib.rs`)

### Summary

The `submit_blocks` function accepts NEAR token deposits to cover on-chain storage costs. As the garbage-collection routine (`run_mainchain_gc`) frees old block headers, the NEAR tokens originally deposited to pay for that storage are released back into the contract's own balance. Because the contract exposes no withdrawal or recovery function, these freed deposits accumulate indefinitely and are permanently locked.

### Finding Description

`submit_blocks` is marked `#[payable]` and charges callers for the net storage increase produced by each batch of headers. [1](#0-0) 

The critical accounting step is:

```rust
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
```

`env::storage_usage()` is measured **after** `run_mainchain_gc` has already removed old headers. [2](#0-1) 

When GC removes as many (or more) bytes than the new headers add, `diff_storage_usage` saturates to `0`, `required_deposit` is `0`, and the **current** caller receives a full refund of their attached deposit. [3](#0-2) 

However, the NEAR tokens deposited by **previous** callers to pay for the storage that was just freed are already sitting in the contract's balance. Those prior depositors receive nothing back. Over successive rounds of submission and GC, the contract's balance grows by exactly the sum of all storage deposits that funded headers that have since been garbage-collected, with no path to recover them.

`run_mainchain_gc` is also independently callable by any account holding `UnrestrictedRunGC`, accelerating the accumulation. [4](#0-3) 

The contract's public API contains no `withdraw`, `drain`, or owner-rescue function anywhere in `lib.rs`. The `Upgradable` trait could theoretically allow a future code deployment to add one, but that is a privileged, multi-step governance action — not an existing recovery mechanism.

### Impact Explanation

NEAR tokens deposited by relayers for storage that is subsequently freed by GC are permanently locked in the contract. The amount grows monotonically over the contract's lifetime: with a default `gc_threshold` of 52,704 blocks and each 80-byte header costing roughly `80 × storage_byte_cost` NEAR, the locked balance compounds with every GC cycle. There is no on-chain path for any party — owner, DAO, or relayer — to recover these funds under the current contract code.

### Likelihood Explanation

GC is triggered automatically on every `submit_blocks` call once the stored chain exceeds `gc_threshold`. [5](#0-4)  The relayer submits blocks continuously by design, so GC fires in normal operation without any adversarial action. The locking is therefore certain to occur in any live deployment.

### Recommendation

Add an owner- or DAO-gated withdrawal function that transfers the contract's surplus balance (i.e., `env::account_balance() - env::storage_usage() * env::storage_byte_cost()`) to a designated recipient. This mirrors the standard emergency-withdrawal pattern and is consistent with the existing role-based access-control infrastructure (`Role::DAO`).

### Proof of Concept

1. Relayer submits batch A (100 headers), attaches 0.08 NEAR. Contract stores 100 headers; 0.08 NEAR is consumed as storage stake.
2. Relayer submits batch B (100 headers). `run_mainchain_gc` removes the 100 headers from batch A. `env::storage_usage()` after GC equals `initial_storage`, so `diff_storage_usage = 0`, `required_deposit = 0`, and the full deposit for batch B is refunded to the relayer.
3. The 0.08 NEAR from step 1 — which paid for storage that no longer exists — remains in the contract's balance.
4. Steps 1–3 repeat every GC cycle. The locked balance grows without bound.
5. No function in the contract allows any account to withdraw this balance. [6](#0-5)

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
