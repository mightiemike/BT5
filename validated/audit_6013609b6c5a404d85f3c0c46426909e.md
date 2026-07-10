### Title
Unprivileged Caller Can Force Premature Garbage Collection of Canonical Block Headers — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function that carries **no caller access control**. Any unprivileged NEAR account can invoke it with an arbitrarily large `batch_size`, forcing the immediate, permanent deletion of all block headers that exceed `gc_threshold` from the canonical chain maps in a single transaction.

---

### Finding Description

The function `run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

The `#[pause]` macro controls access **only when the contract is paused** — it does not restrict who may call the function during normal operation. There is no `#[access_control_any(roles(...))]`, no predecessor check, and no role guard applied to the unpaused path.

The function body permanently removes block headers from three canonical data structures: [2](#0-1) 

Specifically, for each height in the removal range it calls `remove_block_header` (which deletes from `headers_pool` and `mainchain_header_to_height`) and removes the entry from `mainchain_height_to_header`, then advances `mainchain_initial_blockhash` to the new tail. All three mutations are irreversible.

The `batch_size` argument is attacker-controlled. The actual number of headers removed is `min(total_amount_to_remove, batch_size)`: [3](#0-2) 

Passing `u64::MAX` causes the contract to remove every header currently eligible for GC (all headers beyond `gc_threshold`) in one call, rather than the incremental one-batch-per-`submit_blocks` cadence the design intends.

By contrast, `submit_blocks` — the only other state-mutating public entry point — is gated behind `#[trusted_relayer]`: [4](#0-3) 

The asymmetry is the root cause: `run_mainchain_gc` was exposed as a public function for operational flexibility but was never given a matching caller restriction.

---

### Impact Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` both look up the target block in `headers_pool` and `mainchain_header_to_height`: [5](#0-4) 

Once `run_mainchain_gc` deletes a block header, any SPV proof for a transaction in that block will permanently panic with `"block does not belong to the current main chain"` or `"cannot find requested transaction block"`. Because the deletion is irreversible on-chain, no subsequent relayer submission can restore the lost headers. Downstream contracts or users relying on `verify_transaction_inclusion` for finalized cross-chain settlements are permanently broken for the affected height range.

The corrupted canonical mapping is: `mainchain_header_to_height`, `mainchain_height_to_header`, and `headers_pool` — all three are mutated without authorization.

---

### Likelihood Explanation

The entry path requires no special privilege, no staked deposit, and no cryptographic material. Any NEAR account can construct and submit a single function-call transaction targeting `run_mainchain_gc` with `batch_size = 18446744073709551615`. The contract is live on NEAR testnet (per `flow.sh`) and the function is part of the public ABI. Likelihood is **High**.

---

### Recommendation

Add a role-based access control guard to `run_mainchain_gc` so that only authorized accounts (e.g., `Role::DAO` or a dedicated `GCManager` role) can call it directly. For example:

```rust
#[access_control_any(roles(Role::DAO, Role::UnrestrictedRunGC))]
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }
```

Alternatively, make the function `private` (callable only by the contract itself) and rely solely on the internal call from `submit_blocks`.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 100` and submit 200 mainchain block headers via the authorized relayer path.
2. From **any** unprivileged NEAR account, call:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
3. The contract computes `total_amount_to_remove = 200 - 100 = 100` and `selected_amount_to_remove = min(100, u64::MAX) = 100`. It deletes all 100 oldest headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`, and advances `mainchain_initial_blockhash` to height 100.
4. Call `verify_transaction_inclusion` for any transaction in blocks 0–99. The call panics: `"block does not belong to the current main chain"`.
5. The deleted headers cannot be recovered; the SPV verification path is permanently broken for those heights.

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
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

**File:** contract/src/lib.rs (L392-393)
```rust
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
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
