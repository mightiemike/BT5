### Title
Unprivileged Caller Can Aggressively Prune Mainchain Blocks via `run_mainchain_gc`, Permanently Breaking `verify_transaction_inclusion` for Eligible Blocks — (`File: contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` carries no caller-identity restriction. Any unprivileged NEAR account can invoke it with an arbitrarily large `batch_size`, immediately removing every block that is currently eligible for garbage collection. This permanently destroys the `mainchain_header_to_height` and `headers_pool` entries for those blocks, causing `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to panic for any transaction whose containing block was pruned.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. [1](#0-0) 

The `#[pause]` macro controls whether the function is callable when the contract is in a *paused* state. It imposes **no restriction on the caller's identity** when the contract is running normally. There is no `#[private]`, no role check, and no `require!(env::predecessor_account_id() == ...)` guard anywhere in the function body. [2](#0-1) 

The function removes up to `min(total_amount_to_remove, batch_size)` of the oldest mainchain blocks. Because `batch_size` is attacker-supplied and unbounded, a single call with `batch_size = u64::MAX` removes every block currently eligible for GC in one transaction. [3](#0-2) 

For each removed block, `remove_block_header` deletes the entry from `headers_pool`, and `mainchain_height_to_header.remove(&height)` deletes the height-to-hash index entry. The reverse map `mainchain_header_to_height` is also cleared inside `remove_block_header`.

The test suite confirms that a plain `user_account` (holding only `UnrestrictedSubmitBlocks`, not `UnrestrictedRunGC`) successfully calls `run_mainchain_gc` on a live contract: [4](#0-3) 

---

### Impact Explanation

After an attacker prunes a block, any call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction in that block panics:

- `mainchain_header_to_height.get(&args.tx_block_blockhash)` → panics with `"block does not belong to the current main chain"`. [5](#0-4) 

- `headers_pool.get(&args.tx_block_blockhash)` → panics with `"cannot find requested transaction block"`. [6](#0-5) 

In a cross-chain bridge scenario, a user who submitted a Bitcoin transaction and waited for it to accumulate enough confirmations can have their SPV proof permanently invalidated. Once the block is pruned it cannot be re-inserted (the contract has no re-import path), so the user's ability to claim funds on the NEAR side is permanently destroyed. This is a permanent, irreversible state corruption of the canonical-chain verification result, not merely a transient service degradation.

---

### Likelihood Explanation

The entry path requires only a funded NEAR account and a single contract call. No privileged key, no staked relayer role, no social engineering. The attacker pays only gas. The attack is most effective when the mainchain has grown beyond `gc_threshold` (the recommended value is 52 704 blocks ≈ 1 year), at which point all excess blocks are eligible and can be wiped in one call. A griever monitoring the chain can execute this opportunistically whenever the eligible window opens.

---

### Recommendation

Add an explicit caller-identity guard to `run_mainchain_gc` so that only accounts holding a designated role (e.g., `Role::DAO` or a new `GCManager` role) can invoke it directly. The internal call from `submit_blocks` can bypass the guard via a private helper. For example:

```rust
// Public entry point — role-gated
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::DAO, &env::predecessor_account_id())
            || self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id()),
        "Unauthorized: caller lacks GC role"
    );
    self.internal_run_mainchain_gc(batch_size);
}

// Private helper called from submit_blocks
fn internal_run_mainchain_gc(&mut self, batch_size: u64) { /* existing logic */ }
```

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704`.
2. Relayer submits 52705+ mainchain blocks, making at least 1 block eligible for GC.
3. User `alice.near` (no special roles) calls:
   ```
   near call <contract> run_mainchain_gc '{"batch_size": 18446744073709551615}' --accountId alice.near
   ```
4. All eligible blocks are removed from `headers_pool` and `mainchain_height_to_header`.
5. Any subsequent call to `verify_transaction_inclusion` for a transaction in one of those blocks panics with `"block does not belong to the current main chain"`, permanently blocking SPV proof verification for those transactions. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L288-301)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L310-313)
```rust
        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
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

**File:** contract/tests/test_basics.rs (L515-521)
```rust
        let outcome = user_account
            .call(contract.id(), "run_mainchain_gc")
            .args_json(json!({"batch_size": 100}))
            .max_gas()
            .transact()
            .await?;
        assert!(outcome.is_success());
```
