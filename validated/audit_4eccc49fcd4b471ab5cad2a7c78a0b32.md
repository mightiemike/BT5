### Title
Unprivileged Caller Can Aggressively Purge Mainchain Headers via Unrestricted `run_mainchain_gc` — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function that carries no caller access-control guard when the contract is unpaused. Any unprivileged NEAR account can call it directly with an attacker-controlled `batch_size: u64`, immediately removing all headers currently eligible for garbage collection in a single transaction. This bypasses the rate-limiting that `submit_blocks` imposes (which caps GC to `num_of_headers` per call) and permanently deletes block headers from `headers_pool` and `mainchain_height_to_header`, advancing `mainchain_initial_blockhash`. Downstream calls to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for any GC'd block will then panic, causing SPV proof verification to fail for legitimate users.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

This attribute restricts access **only when the contract is paused**. When the contract is running normally (the production state), the function is fully open to any NEAR account. The caller supplies `batch_size: u64` directly: [2](#0-1) 

The GC removes headers from `mainchain_height_to_header` and `headers_pool`, then advances `mainchain_initial_blockhash`: [3](#0-2) 

The intended rate-limiting path is through `submit_blocks`, which calls `run_mainchain_gc(num_of_headers)` — capping removal to the number of headers just submitted per transaction: [4](#0-3) 

A direct public call with `batch_size = u64::MAX` bypasses this cap entirely, removing every header currently eligible for GC (`amount_of_headers_we_store - gc_threshold`) in one atomic transaction.

---

### Impact Explanation

After the attacker's GC call, any `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` call referencing a purged block hash will panic: [5](#0-4) [6](#0-5) 

Downstream NEAR contracts that consume SPV proofs (e.g., cross-chain bridges, token unlock contracts) will receive a panic instead of a boolean result, causing their cross-contract calls to fail. Legitimate users who submitted valid proofs for blocks that were within the GC window but just purged lose the ability to verify those transactions permanently — the headers are gone from on-chain storage and cannot be re-submitted without a full chain replay.

---

### Likelihood Explanation

The entry path is a single permissionless NEAR transaction. No role, stake, or privileged key is required. The contract is deployed on a public network; any account can call `run_mainchain_gc` at any time the contract is unpaused. The attacker needs only to know the contract address and call the function with `batch_size = u64::MAX`. This is trivially achievable by any adversary monitoring the chain.

---

### Recommendation

Add a caller restriction to `run_mainchain_gc` so that direct external calls require a privileged role (e.g., `Role::DAO` or a new `Role::GCManager`), while still allowing the internal call from `submit_blocks` to proceed without a role check. One approach is to split the function into a private `_run_mainchain_gc(batch_size)` used internally and a public wrapper that enforces role-based access:

```rust
// Internal, called by submit_blocks
fn run_mainchain_gc_internal(&mut self, batch_size: u64) { ... }

// Public, role-gated
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[access_control_any(roles(Role::DAO, Role::GCManager))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    self.run_mainchain_gc_internal(batch_size);
}
```

Alternatively, enforce that `batch_size` passed to the public function is bounded by a protocol-defined maximum to prevent bulk purges.

---

### Proof of Concept

**Setup:**
- Contract deployed with `gc_threshold = 52704`
- Relayer has submitted 53704 headers → `amount_of_headers_we_store = 53704`, so 1000 headers are eligible for GC
- A user (e.g., a bridge contract) is about to call `verify_transaction_inclusion_v2` for a transaction in block at height `mainchain_initial_blockhash + 500` (within the eligible-for-GC window)

**Attack:**
1. Attacker (any NEAR account, no role required) calls:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)  // u64::MAX
   ```
2. Contract computes `selected_amount_to_remove = min(1000, u64::MAX) = 1000`
3. All 1000 eligible headers are removed from `headers_pool` and `mainchain_height_to_header` in one call; `mainchain_initial_blockhash` advances by 1000

**Result:**
- The bridge contract calls `verify_transaction_inclusion_v2` for the block at height `initial + 500`
- `mainchain_header_to_height.get(&args.tx_block_blockhash)` returns `None` → contract panics with `"block does not belong to the current main chain"`
- The bridge's cross-contract call fails; the user's funds remain locked [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L298-301)
```rust
        let target_block_height = self
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
