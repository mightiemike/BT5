### Title
Unprivileged Caller Can Aggressively Prune Mainchain Headers via Public `run_mainchain_gc`, Permanently Breaking SPV Proof Verification — (`contract/src/lib.rs`)

---

### Summary

`BtcLightClient::run_mainchain_gc` is a public NEAR contract method with no caller authorization check when the contract is unpaused. Any unprivileged NEAR account can call it with an attacker-controlled `batch_size` of `u64::MAX`, immediately pruning all excess mainchain block headers in a single transaction. This permanently deletes on-chain header data that `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` require, causing those calls to panic for any block in the pruned range. The deletion is irreversible.

---

### Finding Description

`submit_blocks` is the intended entry point for header ingestion. It calls `run_mainchain_gc` internally, deliberately passing `batch_size = num_of_headers` — the count of headers just submitted — as a rate-limiting mechanism:

```rust
// contract/src/lib.rs:175,181
let num_of_headers = headers.len().try_into().unwrap();
// ...
self.run_mainchain_gc(num_of_headers);
``` [1](#0-0) 

This design ensures GC advances the pruning window gradually, at most by the number of headers just added per call. `submit_blocks` is also gated behind `#[trusted_relayer]`, restricting who can call it.

`run_mainchain_gc` itself, however, is separately exposed as a public contract method with no equivalent caller restriction:

```rust
// contract/src/lib.rs:376-377
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [2](#0-1) 

The `#[pause(except(roles(...)))]` attribute only restricts calls when the contract is **paused**. During normal (unpaused) operation, any NEAR account can call `run_mainchain_gc` with any `batch_size`. The function computes:

```rust
// contract/src/lib.rs:392-393
let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
``` [3](#0-2) 

With `batch_size = u64::MAX`, `selected_amount_to_remove` equals `total_amount_to_remove` — the full excess. The loop then permanently deletes every header in that range from both `headers_pool` and `mainchain_height_to_header`, and advances `mainchain_initial_blockhash` to the new oldest block:

```rust
// contract/src/lib.rs:407-414
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
// ...
self.mainchain_initial_blockhash = self
    .mainchain_height_to_header
    .get(&end_removal_height)
    ...
``` [4](#0-3) 

Both SPV proof functions look up the target block in the maps that GC clears. `verify_transaction_inclusion` panics with `"block does not belong to the current main chain"` if `mainchain_header_to_height` no longer contains the block hash:

```rust
// contract/src/lib.rs:299-301
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [5](#0-4) 

`verify_transaction_inclusion_v2` panics with `"cannot find requested transaction block"` if `headers_pool` no longer contains the block:

```rust
// contract/src/lib.rs:353-356
let header = self
    .headers_pool
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
``` [6](#0-5) 

Both deletions are permanent — there is no mechanism to re-insert pruned headers.

---

### Impact Explanation

Any block header that was legitimately within the `gc_threshold` window but in the "excess" zone above it can be immediately and permanently pruned by an attacker. Any downstream consumer (cross-chain bridge, DeFi protocol, or user) that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a pruned block will receive a panic instead of a proof result — permanently, since the data cannot be recovered. The `mainchain_initial_blockhash` canonical state variable is also advanced to an attacker-chosen position, desynchronizing the contract's view of its own oldest stored block from what the relayer expects.

---

### Likelihood Explanation

The attack requires no privileged role, no staked assets, and no special knowledge beyond knowing the contract address. Any NEAR account can call `run_mainchain_gc` with `batch_size = u64::MAX` in a single transaction. The precondition — that the mainchain has grown beyond `gc_threshold` — is the normal steady-state of a live deployment (the recommended `gc_threshold` of 52704 blocks is exceeded within roughly a year of operation). The attack is cheap (one NEAR transaction, no deposit required) and irreversible.

---

### Recommendation

Add a caller authorization check to `run_mainchain_gc` so that only trusted roles (e.g., `Role::DAO` or a new dedicated `GCManager` role) can invoke it directly. The internal call from `submit_blocks` should bypass this check. For example:

```rust
// Before:
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }

// After: restrict direct external calls to authorized roles
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::DAO, &env::predecessor_account_id())
            || self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id())
            || env::predecessor_account_id() == env::current_account_id(),
        "Unauthorized: only DAO or GC role may call run_mainchain_gc directly"
    );
    // ... existing logic
}
```

Alternatively, split the function into a private `run_mainchain_gc_internal` called by `submit_blocks`, and a separate role-gated public wrapper.

---

### Proof of Concept

**Precondition:** The mainchain has grown to `gc_threshold + N` blocks (normal after sustained relayer operation).

**Attacker steps (no privileged role required):**

1. Attacker observes that `get_mainchain_size()` returns a value greater than `gc_threshold`.
2. Attacker calls `run_mainchain_gc` with `batch_size = u64::MAX` from any NEAR account.
3. The contract immediately deletes all `N` excess headers from `headers_pool` and `mainchain_height_to_header`, and advances `mainchain_initial_blockhash`.
4. Any subsequent call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` referencing any of those `N` pruned blocks panics — permanently.

**Contrast with intended behavior:** The relayer calling `submit_blocks` with 10 headers would have triggered `run_mainchain_gc(10)`, removing at most 10 blocks per batch, giving downstream consumers time to complete their proof verifications before those blocks aged out.

### Citations

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L299-301)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
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

**File:** contract/src/lib.rs (L391-393)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L407-414)
```rust
                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```
