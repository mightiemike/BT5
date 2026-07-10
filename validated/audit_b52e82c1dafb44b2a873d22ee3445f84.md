### Title
Fork Headers Accumulated for Free via GC-Offset Storage Accounting in `submit_blocks` — (File: `contract/src/lib.rs`)

---

### Summary

`submit_blocks` measures the net storage delta **after** `run_mainchain_gc` has already freed mainchain-header storage. Because GC removes three storage slots per mainchain header (one each from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`) while a fork header adds only one slot (to `headers_pool`), the net storage change saturates to zero when GC is active. The caller receives a full deposit refund yet the fork headers remain in `headers_pool` permanently — they are never touched by GC. A trusted relayer can exploit this to accumulate an unbounded number of orphaned fork headers at zero net cost, mirroring the external report's pattern of reserving a resource, getting it refunded, and repeating indefinitely.

---

### Finding Description

**Storage accounting order in `submit_blocks`** [1](#0-0) 

```
let initial_storage = env::storage_usage();          // (1) snapshot before
for header in headers { self.submit_block_header(…) } // (2) fork headers written
self.run_mainchain_gc(num_of_headers);               // (3) GC frees mainchain slots
let diff_storage_usage =
    env::storage_usage().saturating_sub(initial_storage); // (4) net delta
```

Step (3) runs before step (4). GC removes up to `batch_size` (= `num_of_headers`) mainchain entries via `remove_block_header` + `mainchain_height_to_header.remove`: [2](#0-1) 

`remove_block_header` deletes from **both** `mainchain_header_to_height` and `headers_pool`: [3](#0-2) 

`store_fork_header` writes only to `headers_pool`: [4](#0-3) 

**Storage slot arithmetic per N submitted fork headers when GC removes N mainchain headers:**

| Operation | `headers_pool` | `mainchain_height_to_header` | `mainchain_header_to_height` |
|---|---|---|---|
| N fork headers added | +N | 0 | 0 |
| N mainchain headers GC'd | −N | −N | −N |
| **Net** | **0** | **−N** | **−N** |

Total net delta = **−2N** slots → `saturating_sub` clamps to **0** → `required_deposit = 0` → full refund issued. [5](#0-4) 

**Fork headers are never GC'd.** `run_mainchain_gc` iterates only `mainchain_height_to_header`, which never contains fork hashes. Fork entries in `headers_pool` are orphaned forever. [6](#0-5) 

---

### Impact Explanation

1. **Unbounded permanent storage growth**: Each attack round deposits N fork headers into `headers_pool` at zero net cost. Repeated calls accumulate headers without limit. NEAR storage is finite and priced; bloating it degrades or eventually halts the contract.

2. **Gas exhaustion during legitimate reorg**: `reorg_chain` traverses fork headers by following `prev_block_hash` links through `headers_pool` until it finds a mainchain ancestor. [7](#0-6) 

An attacker who pre-populates `headers_pool` with a long chain of fork headers at the same branch point forces any future legitimate reorg to traverse all of them, exhausting NEAR gas and causing the reorg call to fail — permanently corrupting the contract's ability to follow the heaviest chain.

3. **Broken storage-cost invariant**: The contract's deposit model assumes callers pay for net storage consumed. The GC-before-measurement ordering breaks this invariant, allowing free writes.

---

### Likelihood Explanation

**Preconditions:**
- Attacker must be a trusted relayer. The `#[trusted_relayer]` macro from `omni_utils` implements a permissionless staking model — any account that stakes the required bond becomes a trusted relayer. No privileged key is needed. [8](#0-7) 

- The mainchain must be above `gc_threshold` (default 52,704 blocks ≈ 1 year of Bitcoin). This is the **normal operating state** of a live deployment; the contract is designed to run continuously past this threshold. [9](#0-8) 

Both conditions are routinely satisfied in production. The attack requires only valid PoW headers (or a deployment with `skip_pow_verification = true`), which a relayer already possesses by definition.

---

### Recommendation

Capture the storage snapshot **after** GC runs, so the deposit only covers storage that fork headers actually add net of GC:

```rust
// Run GC first (it frees mainchain storage)
self.run_mainchain_gc(num_of_headers);

// Snapshot storage after GC, before submitting new headers
let initial_storage = env::storage_usage();

for header in headers {
    self.submit_block_header(header, self.skip_pow_verification);
}

let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
```

Alternatively, track fork-header storage separately and charge for it independently of GC activity, ensuring fork headers are never stored for free regardless of GC timing.

Additionally, implement a GC mechanism for orphaned fork headers (e.g., prune fork branches whose tip chainwork falls below the mainchain tip by more than `gc_threshold` blocks).

---

### Proof of Concept

**Setup**: Contract deployed on NEAR testnet with `gc_threshold = 52704`. Mainchain has grown to height 52,800 (i.e., 96 headers above threshold). Attacker has staked to become a trusted relayer.

**Attack steps:**

1. Attacker constructs N = 96 fork headers, all branching from the genesis block (or any old mainchain block below the GC window). Each fork header has valid PoW (or `skip_pow_verification = true`).

2. Attacker calls `submit_blocks(fork_headers_96)` with a small attached deposit (e.g., 1 yoctoNEAR).

3. Inside `submit_blocks`:
   - `initial_storage` = S (snapshot taken)
   - 96 fork headers written to `headers_pool` → storage = S + 96 slots
   - `run_mainchain_gc(96)` fires: mainchain is 96 above threshold → removes 96 mainchain headers → frees 96 × 3 = 288 slots → storage = S + 96 − 288 = S − 192
   - `diff_storage_usage = (S − 192).saturating_sub(S) = 0`
   - `required_deposit = 0`
   - Full deposit refunded

4. Result: 96 fork headers permanently reside in `headers_pool`. Attacker paid 0 net NEAR for storage.

5. Repeat: each round adds 96 more orphaned fork headers. After K rounds, `headers_pool` contains 96K orphaned entries. Any future reorg traversing these branches exhausts NEAR gas and panics, preventing the contract from ever following a heavier fork — a permanent integrity failure in the light client's fork-choice logic.

### Citations

**File:** contract/src/lib.rs (L113-114)
```rust
    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L173-197)
```rust
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
```

**File:** contract/src/lib.rs (L391-415)
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
        }
```

**File:** contract/src/lib.rs (L616-643)
```rust
        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
        {
            let prev_block_hash = fork_header_cursor.block_header.prev_block_hash;
            let current_block_hash = fork_header_cursor.block_hash;
            let current_height = fork_header_cursor.block_height;

            // Inserting the fork block into the main chain, if some mainchain block is occupying
            // this height let's save its hashcode
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);

            // If we found a mainchain block at the current height than remove this block from the
            // header pool and from the header -> height map
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L665-667)
```rust
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```
