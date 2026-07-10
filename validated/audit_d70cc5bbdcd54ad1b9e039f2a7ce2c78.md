### Title
`UnrestrictedSubmitBlocks` Role Rendered Non-Functional When Paused Due to Internal `run_mainchain_gc` Pause Check — (File: `contract/src/lib.rs`)

---

### Summary

`submit_blocks` is intended to be callable by accounts holding `Role::UnrestrictedSubmitBlocks` even when the contract is paused. However, `submit_blocks` internally calls the public method `run_mainchain_gc`, which carries its own independent `#[pause(except(roles(Role::UnrestrictedRunGC)))]` guard. When the contract is paused, the pause check inside `run_mainchain_gc` fires against the original external caller's roles. A caller with only `UnrestrictedSubmitBlocks` (not `UnrestrictedRunGC`) will always panic at that inner check, making the bypass role completely non-functional.

---

### Finding Description

`submit_blocks` is decorated with `#[pause]` and the `#[trusted_relayer]` macro's `bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks)` is intended to allow privileged relayers to submit blocks even when the contract is paused. [1](#0-0) 

Inside `submit_blocks`, after processing all headers, the function unconditionally calls the public method `run_mainchain_gc`: [2](#0-1) 

`run_mainchain_gc` is a separate public method with its own pause guard: [3](#0-2) 

This call is a direct Rust method call — not a cross-contract call — so `env::predecessor_account_id()` inside `run_mainchain_gc` still resolves to the original external caller. The `near_plugins` `#[pause(except(roles(Role::UnrestrictedRunGC)))]` macro checks whether that caller holds `Role::UnrestrictedRunGC`. A caller holding only `Role::UnrestrictedSubmitBlocks` does not satisfy this check, causing a panic.

The role comment confirms the intended design: [4](#0-3) 

The two bypass roles are disjoint: `UnrestrictedSubmitBlocks` bypasses `submit_blocks`'s outer pause guard, while `UnrestrictedRunGC` bypasses `run_mainchain_gc`'s inner pause guard. There is no mechanism that grants both simultaneously, and `submit_blocks` always invokes `run_mainchain_gc` unconditionally.

---

### Impact Explanation

When the contract is paused, any account granted `Role::UnrestrictedSubmitBlocks` — the role specifically designed to keep block submission alive during a pause — cannot successfully call `submit_blocks`. Every such call panics inside `run_mainchain_gc`. The canonical chain cannot be updated, SPV proofs cannot be verified against fresh headers, and the light client is effectively fully halted despite the existence of a bypass role intended to prevent exactly this outcome.

---

### Likelihood Explanation

The contract is designed with an operational pause mechanism and a dedicated bypass role (`UnrestrictedSubmitBlocks`) for emergency or maintenance scenarios. The failure manifests precisely when the bypass role is exercised — i.e., when the contract is paused and a trusted relayer or DAO-delegated account attempts to keep the chain synchronized. The bug is deterministic and reproducible with no special attacker capability required; any account holding `UnrestrictedSubmitBlocks` but not `UnrestrictedRunGC` will trigger it.

---

### Recommendation

Replace the call to the public `run_mainchain_gc` inside `submit_blocks` with a call to an internal helper that contains the GC logic without the pause guard — analogous to the fix in the reference report where `_setDistributionFactors` was changed to call `_updateDistributionSpeed` directly instead of the public `updateDistributionSpeed`.

Concretely, extract the body of `run_mainchain_gc` into a private `fn run_mainchain_gc_inner(&mut self, batch_size: u64)` with no `#[pause]` attribute, have `submit_blocks` call `self.run_mainchain_gc_inner(num_of_headers)`, and keep the public `run_mainchain_gc` as a thin wrapper that applies the pause guard and delegates to the inner function.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature) and pause it via a `PauseManager` account.
2. Grant an account `Role::UnrestrictedSubmitBlocks` but **not** `Role::UnrestrictedRunGC`.
3. Have that account call `submit_blocks` with one valid header and sufficient deposit.
4. Observe: the call panics inside `run_mainchain_gc` because the caller lacks `UnrestrictedRunGC`, even though the outer `submit_blocks` pause guard was successfully bypassed.
5. Grant the same account `Role::UnrestrictedRunGC` in addition and repeat — the call succeeds, confirming the root cause. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L44-45)
```rust
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
```

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
