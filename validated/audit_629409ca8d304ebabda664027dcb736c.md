### Title
`UnrestrictedSubmitBlocks` Pause Bypass Is Broken by Internal `run_mainchain_gc` Access Guard — (`File: contract/src/lib.rs`)

---

### Summary

`submit_blocks` is decorated with a pause-bypass for `Role::UnrestrictedSubmitBlocks`, but it unconditionally calls `self.run_mainchain_gc(...)` internally. `run_mainchain_gc` carries its own independent `#[pause(except(roles(Role::UnrestrictedRunGC)))]` guard. Because NEAR's `near-plugins` pause macro evaluates `env::predecessor_account_id()` — which remains the original external caller even for intra-contract Rust calls — any relayer holding only `Role::UnrestrictedSubmitBlocks` will have the transaction revert inside `run_mainchain_gc` whenever the contract is paused. The bypass role is rendered completely non-functional.

---

### Finding Description

`submit_blocks` is the sole public entry point for relayers to push Bitcoin block headers on-chain. It carries two guards:

- `#[pause]` — blocks calls when the contract is paused
- `#[trusted_relayer]` — restricts callers to the trusted-relayer set; accounts with `Role::UnrestrictedSubmitBlocks` bypass both the pause and the relayer check [1](#0-0) 

After processing all submitted headers, `submit_blocks` unconditionally calls `self.run_mainchain_gc(num_of_headers)`: [2](#0-1) 

`run_mainchain_gc` is a separately guarded public function: [3](#0-2) 

Its `#[pause(except(roles(Role::UnrestrictedRunGC)))]` attribute inserts a pause check at the top of the function body. In NEAR's `near-plugins` implementation, this check reads `env::predecessor_account_id()` to determine whether the caller holds the bypass role. Because the call from `submit_blocks` to `run_mainchain_gc` is a direct Rust method call (not a cross-contract call), `env::predecessor_account_id()` is still the original external relayer account — not the contract itself.

A relayer that holds `Role::UnrestrictedSubmitBlocks` but not `Role::UnrestrictedRunGC` will:

1. Pass the `submit_blocks` pause/relayer check ✓
2. Process all submitted headers ✓
3. Reach `self.run_mainchain_gc(num_of_headers)` and **panic** because the relayer lacks `Role::UnrestrictedRunGC` ✗

The entire transaction reverts. No headers are stored. The `UnrestrictedSubmitBlocks` role is structurally broken during any pause.

The two roles are defined as independent, separate enum variants with no documented coupling: [4](#0-3) 

The `trusted_relayer` macro configuration also treats them as independent bypass paths: [5](#0-4) 

---

### Impact Explanation

The `UnrestrictedSubmitBlocks` role exists precisely to keep the light client operational during an emergency pause — for example, to allow a trusted relayer to continue tracking the Bitcoin chain while an exploit is being mitigated. Because the role is non-functional (every `submit_blocks` call reverts at the GC step), the light client cannot advance its chain tip during any pause. Downstream consumers calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will receive stale or absent confirmation data. Any protocol or bridge that depends on SPV proof freshness during a pause event is effectively frozen for the duration of the pause. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

The pause mechanism is an expected operational tool (emergency response, upgrade preparation). Every time it is activated, the `UnrestrictedSubmitBlocks` bypass silently fails. The failure is not obvious: the role is correctly granted, the outer guard passes, but the inner guard reverts. No special attacker action is required — the bug is triggered by the normal operational act of pausing the contract.

---

### Recommendation

Either:

1. **Remove the `#[pause]` guard from `run_mainchain_gc`** when it is called internally from `submit_blocks`, by extracting the GC logic into an unguarded private helper and calling that directly:

```rust
// private, no pause guard
fn run_mainchain_gc_inner(&mut self, batch_size: u64) { ... }

#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    self.run_mainchain_gc_inner(batch_size);
}
```

And in `submit_blocks`, call `self.run_mainchain_gc_inner(num_of_headers)` instead.

2. **Or**, document and enforce that `Role::UnrestrictedSubmitBlocks` must always be granted together with `Role::UnrestrictedRunGC`, and update the `trusted_relayer` bypass configuration accordingly.

Option 1 is preferred because it preserves role separation and matches the intent of the two independent bypass roles.

---

### Proof of Concept

1. Deploy the contract (bitcoin feature).
2. Pause the contract via a `PauseManager` account.
3. Grant a relayer account `Role::UnrestrictedSubmitBlocks` but **not** `Role::UnrestrictedRunGC`.
4. Have the relayer call `submit_blocks` with a valid batch of headers.
5. Observe: the call panics inside `run_mainchain_gc` with the pause-guard failure, despite the relayer holding the designated bypass role. No headers are stored. The chain tip does not advance. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L43-46)
```rust
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
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
