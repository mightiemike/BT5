### Title
Unprivileged Caller Can Trigger `run_mainchain_gc` to Permanently Delete Mainchain Block Headers - (File: `contract/src/lib.rs`)

### Summary

`run_mainchain_gc` is a public, state-mutating function that permanently deletes mainchain block headers from contract storage. Its only access guard is `#[pause(except(roles(Role::UnrestrictedRunGC)))]`, which restricts access only when the contract is **paused**. During normal (unpaused) operation, any unprivileged NEAR account can call it with an arbitrary `batch_size`, triggering irreversible deletion of the oldest mainchain headers. This is the direct analog to the OverlayToken burn vulnerability: a destructive operation on shared state is callable by any account without a role check.

### Finding Description

`submit_blocks` is correctly gated behind `#[trusted_relayer]`, requiring the caller to hold `UnrestrictedSubmitBlocks` or `DAO` role. [1](#0-0) 

`run_mainchain_gc`, however, carries only the `#[pause]` decorator:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [2](#0-1) 

The `#[pause(except(...))]` macro from `near-plugins` only enforces the role check when the contract is in the paused state. When the contract is running normally (the default and expected production state), the function is callable by **any** NEAR account with no role requirement.

Inside the function, for every height in the removal range, `remove_block_header` is called and the height-to-hash mapping entry is deleted:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
``` [3](#0-2) 

After the loop, `mainchain_initial_blockhash` is advanced to the new oldest block: [4](#0-3) 

These deletions are permanent and irreversible. The removed headers are gone from `headers_pool` and `mainchain_height_to_header`.

### Impact Explanation

1. **SPV proof breakage**: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height`. If the block has been GC'd by an attacker, the call panics with "block does not belong to the current main chain", permanently invalidating SPV proofs for transactions in those blocks. [5](#0-4) 

2. **Chain reorg breakage**: The CLAUDE.md explicitly documents: *"If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound`"*. An attacker can trigger GC immediately after a batch submission to ensure that any subsequent fork resolution panics. [6](#0-5) 

3. **Relayer storage deposit loss**: Relayers pay a storage deposit for each submitted block. Premature GC by an attacker destroys that storage without refunding the depositor. [7](#0-6) 

### Likelihood Explanation

The entry path requires no special role, no staked funds, and no cryptographic material. Any NEAR account can call `run_mainchain_gc(batch_size)` in a single transaction at any time the contract is not paused. The contract is designed to run unpaused in production. The attack is trivially repeatable and costs only gas.

### Recommendation

Add a role check to `run_mainchain_gc` that mirrors the restriction on `submit_blocks`. The simplest fix is to require the caller to hold `UnrestrictedRunGC` (or `DAO`) even when the contract is not paused, by adding an explicit `acl_is_caller_role` guard or by replacing the `#[pause]` decorator with a combined `#[pause]` + role assertion:

```rust
// Require caller to hold UnrestrictedRunGC or DAO role unconditionally
require!(
    self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id())
        || self.acl_has_role(Role::DAO, &env::predecessor_account_id()),
    "Unauthorized: missing UnrestrictedRunGC or DAO role"
);
```

Alternatively, since `run_mainchain_gc` is already called internally by `submit_blocks` (which is role-gated), the public external entry point can simply be removed or restricted to privileged callers only.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704` and submit enough blocks so that `mainchain_size > gc_threshold`.
2. From a freshly created NEAR account with **no roles**, call:
   ```
   run_mainchain_gc({ "batch_size": 1000 })
   ```
3. Observe that the call succeeds and `get_mainchain_size` decreases by up to 1000.
4. Repeat until the oldest blocks needed for a pending SPV proof are deleted.
5. Call `verify_transaction_inclusion` for a transaction in a deleted block — the call panics, confirming the proof is permanently broken.

The test suite itself demonstrates that `run_mainchain_gc` is callable by the `user_account` (which holds only `UnrestrictedSubmitBlocks`, not `UnrestrictedRunGC`) and succeeds, confirming the absence of a caller restriction on the public entry point. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L182-188)
```rust
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L407-408)
```rust
                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
```

**File:** contract/src/lib.rs (L411-414)
```rust
            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
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
