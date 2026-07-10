### Title
Unauthorized Access to `run_mainchain_gc` Allows Any Caller to Prematurely Purge Mainchain Block Headers — (`File: contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function on `BtcLightClient` that permanently removes block headers from the canonical mainchain. It carries only a `#[pause]` guard, which controls behavior when the contract is paused but imposes **no caller identity restriction** when the contract is live. Any unprivileged NEAR account can invoke it with an arbitrary `batch_size`, causing premature deletion of mainchain headers and breaking SPV proof verification for any block that falls within the removed range.

---

### Finding Description

`submit_blocks` — the privileged header-submission entry point — is protected by both `#[pause]` and `#[trusted_relayer]`, ensuring only enrolled relayers can alter chain state. [1](#0-0) 

`run_mainchain_gc`, by contrast, carries only `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. The `except(roles(...))` clause only controls who may call the function **while the contract is paused**; it does not restrict callers when the contract is unpaused. There is no `#[trusted_relayer]`, no `#[access_control_any]`, and no manual `env::predecessor_account_id()` check. [2](#0-1) 

The function permanently deletes entries from `headers_pool` and `mainchain_height_to_header`, and advances `mainchain_initial_blockhash` forward: [3](#0-2) 

The `Role::UnrestrictedRunGC` role comment in the `Role` enum confirms the intent was only to allow GC during a pause — not to gate normal-operation access: [4](#0-3) 

The integration test confirms a plain `user_account` (no roles granted) can call `run_mainchain_gc` successfully on a live contract: [5](#0-4) 

---

### Impact Explanation

An attacker calls `run_mainchain_gc(batch_size: u64::MAX)`. The function removes up to `amount_of_headers_we_store - gc_threshold` headers in a single transaction — the maximum excess above the retention threshold. After removal, any call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` that references a GC'd block hash panics with `"cannot find requested transaction block"`: [6](#0-5) 

This permanently corrupts the light client's historical state. Downstream contracts or users relying on SPV proofs for blocks in the removed range receive hard panics rather than valid verification results. The `mainchain_initial_blockhash` is also advanced, so the removed range can never be recovered without a full contract re-initialization.

---

### Likelihood Explanation

The entry point is fully open: any NEAR account with enough gas can call `run_mainchain_gc`. No staking, no role, no deposit is required. The attacker only needs to know the contract account ID, which is public. The attack is repeatable — every time the relayer pushes new blocks beyond `gc_threshold`, the attacker can immediately drain the excess, keeping the retained window at its minimum and maximizing the set of blocks unavailable for SPV proofs.

---

### Recommendation

Add a caller-identity guard matching the pattern used on `submit_blocks`. The simplest fix is to apply the `#[trusted_relayer]` macro (or an equivalent `#[access_control_any(roles(Role::DAO, Role::RelayerManager))]` check) to `run_mainchain_gc`, so that only enrolled relayers or DAO accounts can trigger GC externally. The internal call from `submit_blocks` (line 181) is already inside a `#[trusted_relayer]`-gated function and remains unaffected. [7](#0-6) 

---

### Proof of Concept

```rust
// Any unprivileged NEAR account can execute this.
// Precondition: contract is live (unpaused), chain has grown beyond gc_threshold.

let attacker = sandbox.dev_create_account().await?;   // no roles granted

let outcome = attacker
    .call(contract.id(), "run_mainchain_gc")
    .args_json(json!({"batch_size": u64::MAX}))
    .max_gas()
    .transact()
    .await?;

assert!(outcome.is_success());   // succeeds — no access control blocks it

// Now verify_transaction_inclusion for any GC'd block panics:
// "cannot find requested transaction block"
```

The existing test at `contract/tests/test_basics.rs:515–521` already demonstrates this path succeeds for an unprivileged `user_account`, confirming the missing guard is not a test artifact. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L45-46)
```rust
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
```

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L181-181)
```rust
        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L353-356)
```rust
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

**File:** contract/src/lib.rs (L401-414)
```rust
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
