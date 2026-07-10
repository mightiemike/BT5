### Title
Unprivileged Caller Can Prune Mainchain Headers via `run_mainchain_gc` - (File: `contract/src/lib.rs`)

---

### Summary

`BtcLightClient::run_mainchain_gc` is a public, state-mutating function that permanently deletes block headers from `headers_pool` and `mainchain_height_to_header`, and advances `mainchain_initial_blockhash`. It carries only a `#[pause]` attribute, which restricts calls only when the contract is **paused**. When the contract is live (unpaused), any unprivileged NEAR account can call it with an arbitrary `batch_size`, triggering maximum-depth pruning of the canonical chain.

---

### Finding Description

`submit_blocks` is correctly gated behind `#[trusted_relayer]`, which enforces that only accounts holding `Role::UnrestrictedSubmitBlocks` or `Role::DAO` may submit headers. Internally, `submit_blocks` calls `run_mainchain_gc` as a side-effect after each batch. [1](#0-0) 

However, `run_mainchain_gc` is also exposed as a standalone public method with no caller restriction: [2](#0-1) 

The `#[pause(except(roles(Role::UnrestrictedRunGC)))]` attribute only gates the function when the contract is paused. When unpaused — the normal production state — the function is callable by any NEAR account with no role check whatsoever.

Inside the function, the attacker-controlled `batch_size` determines how many headers are removed. The actual removal count is capped at `amount_of_headers_we_store - gc_threshold`, so the attacker can prune the chain all the way down to the `gc_threshold` floor in a single call: [3](#0-2) 

Each pruned header is permanently deleted from `headers_pool` and `mainchain_height_to_header`, and `mainchain_initial_blockhash` is permanently advanced.

---

### Impact Explanation

Both SPV verification entry points (`verify_transaction_inclusion` and `verify_transaction_inclusion_v2`) look up the target block in `mainchain_header_to_height` and `headers_pool`: [4](#0-3) 

Once a block header is pruned by `run_mainchain_gc`, these lookups panic with `"block does not belong to the current main chain"` or `"cannot find requested transaction block"`. The deletion is permanent — there is no way to re-insert a pruned header. Any downstream contract or user relying on SPV proof verification for a block that falls below the new `mainchain_initial_blockhash` will have their proof permanently rejected, even if the transaction was legitimately confirmed with sufficient depth.

The broken invariant: `mainchain_initial_blockhash` and the set of stored headers must only advance as a consequence of trusted relayer submissions, not arbitrary external calls.

---

### Likelihood Explanation

The attack requires no special role, no deposit, and no prior knowledge beyond the contract's public ABI. Any NEAR account can call `run_mainchain_gc(u64::MAX)` at any time the contract is unpaused. The function is listed in the public ABI and is trivially discoverable. A griefing attacker targeting a specific bridge user's pending SPV proof can time the call to prune the relevant block immediately after it is submitted but before the proof is verified.

---

### Recommendation

Add `#[trusted_relayer]` (or an equivalent role check such as `Role::UnrestrictedRunGC` or `Role::DAO`) to `run_mainchain_gc` so that only authorized accounts can invoke it directly. The internal call from `submit_blocks` already bypasses the pause check, so the same bypass mechanism can be applied to the role check for the internal path:

```rust
// Before
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {

// After
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[access_control_any(roles(Role::DAO, Role::UnrestrictedRunGC, Role::RelayerManager))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

Alternatively, make `run_mainchain_gc` a private method and expose a separate, role-gated public wrapper if external GC triggering is a desired feature.

---

### Proof of Concept

```rust
// Any unprivileged NEAR account can call this:
let outcome = attacker_account
    .call(contract.id(), "run_mainchain_gc")
    .args_json(json!({ "batch_size": u64::MAX }))
    .transact()
    .await?;
// outcome.is_success() == true

// Now verify_transaction_inclusion for any pruned block panics:
let proof_outcome = any_account
    .call(contract.id(), "verify_transaction_inclusion_v2")
    .args_borsh(proof_for_pruned_block)
    .transact()
    .await?;
// Fails with: "block does not belong to the current main chain"
```

The attacker needs no role, no deposit, and no privileged key. The call succeeds on an unpaused contract and permanently corrupts the canonical header set, making all SPV proofs for pruned blocks permanently unverifiable.

### Citations

**File:** contract/src/lib.rs (L166-181)
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

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L391-414)
```rust
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
```
