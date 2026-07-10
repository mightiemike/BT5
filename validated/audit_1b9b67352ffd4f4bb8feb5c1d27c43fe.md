### Title
Incomplete State Cleanup in `run_mainchain_gc()` Leaves Stale Entries in `mainchain_header_to_height` — (`File: contract/src/lib.rs`)

### Summary
`run_mainchain_gc()` removes GC'd block entries from `headers_pool` and `mainchain_height_to_header` but never removes the corresponding entries from `mainchain_header_to_height`. This is a direct analog to the `removeSession()` incomplete cleanup bug: two parallel mappings are maintained on insertion but only one is cleaned on deletion, breaking the invariant that `mainchain_header_to_height` reflects only currently-stored main chain blocks.

### Finding Description

The contract maintains three parallel data structures for main chain blocks:

- `mainchain_height_to_header: LookupMap<u64, H256>` — height → blockhash
- `mainchain_header_to_height: LookupMap<H256, u64>` — blockhash → height
- `headers_pool: LookupMap<H256, ExtendedHeader>` — blockhash → full header

All three are populated together when a block is added to the main chain via `store_block_header`. However, `run_mainchain_gc()` only removes from two of them: [1](#0-0) 

```rust
for height in start_removal_height..end_removal_height {
    let blockhash = &self
        .mainchain_height_to_header
        .get(&height)
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

    self.remove_block_header(blockhash);           // removes from headers_pool
    self.mainchain_height_to_header.remove(&height); // removes height→hash mapping
    // ❌ mainchain_header_to_height.remove(blockhash) is NEVER called
}
``` [2](#0-1) 

After GC runs, `mainchain_header_to_height` retains stale entries for every removed block. These entries are never reclaimed.

### Impact Explanation

`verify_transaction_inclusion` uses `mainchain_header_to_height` as its sole security gate to confirm a block belongs to the current main chain: [3](#0-2) 

```rust
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

Because stale entries remain after GC, a GC'd block hash passes this check. The function then attempts to fetch the full header from `headers_pool`, which no longer contains the block, causing an unconditional panic: [4](#0-3) 

```rust
let header = self
    .headers_pool
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
```

Additionally, the public `get_height_by_block_hash` view function returns a non-`None` result for GC'd blocks: [5](#0-4) 

Consumer contracts that use `get_height_by_block_hash(...).is_some()` as a main-chain membership check will incorrectly treat GC'd blocks as currently stored and valid. The invariant "a block hash present in `mainchain_header_to_height` is currently stored on the main chain" is permanently broken after the first GC run.

Additionally, `mainchain_header_to_height` grows monotonically and is never pruned, causing unbounded storage growth proportional to the total number of blocks ever processed.

### Likelihood Explanation

GC runs automatically on every `submit_blocks` call: [6](#0-5) 

This means the broken invariant is triggered in normal production operation by any relayer submitting blocks once the chain exceeds `gc_threshold` blocks. No special attacker action is required to create the stale state — it accumulates continuously. Any unprivileged NEAR caller can then call `verify_transaction_inclusion` or `get_height_by_block_hash` with a GC'd block hash to observe the broken behavior.

### Recommendation

In `run_mainchain_gc()`, add a removal from `mainchain_header_to_height` inside the GC loop, symmetric with the existing `mainchain_height_to_header.remove(&height)` call:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
self.mainchain_header_to_height.remove(blockhash); // add this
``` [1](#0-0) 

### Proof of Concept

1. Initialize the contract with `gc_threshold = 100`.
2. Submit 200 blocks via `submit_blocks`. GC fires automatically, removing blocks at heights 0–99 from `headers_pool` and `mainchain_height_to_header`.
3. Call `get_height_by_block_hash(hash_of_block_at_height_50)` — returns `Some(50)` despite the block being GC'd. The stale entry in `mainchain_header_to_height` is confirmed.
4. Call `verify_transaction_inclusion` with `tx_block_blockhash = hash_of_block_at_height_50` — the function passes the `mainchain_header_to_height` check (stale entry), then panics at `headers_pool.get()` with `"cannot find requested transaction block"` instead of the expected `"block does not belong to the current main chain"` message.
5. A consumer contract relying on `get_height_by_block_hash(...).is_some()` as a main-chain guard incorrectly accepts the GC'd block as valid. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L181-181)
```rust
        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L217-220)
```rust
    #[allow(clippy::needless_pass_by_value)]
    pub fn get_height_by_block_hash(&self, blockhash: H256) -> Option<u64> {
        self.mainchain_header_to_height.get(&blockhash)
    }
```

**File:** contract/src/lib.rs (L299-302)
```rust
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

**File:** contract/src/lib.rs (L377-416)
```rust
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
