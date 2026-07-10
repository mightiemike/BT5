### Title
GC-Evicted Block Headers Permanently Break `verify_transaction_inclusion` for Downstream Consumers — (`contract/src/lib.rs`)

### Summary

`run_mainchain_gc` permanently removes old block headers from on-chain storage. `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` contain only a weak guard (`args.confirmations <= self.gc_threshold`) that does not prevent a caller from referencing a block that has already been evicted. Once a block is GC'd it cannot be re-submitted (its predecessor is also gone), so any downstream NEAR contract that gates asset release on a successful proof call is permanently blocked with no recovery path.

### Finding Description

**Root cause — insufficient guard in `verify_transaction_inclusion`**

The only pre-flight check against GC is:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

This only validates the *requested confirmation count*, not whether the *specific target block* is still present in storage. A block at height H can satisfy `confirmations <= gc_threshold` while having already been evicted if the chain tip has advanced far enough.

**GC eviction path**

`run_mainchain_gc` removes the oldest mainchain blocks from both `mainchain_height_to_header` and `headers_pool`:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
``` [2](#0-1) 

`remove_block_header` deletes the entry from both `mainchain_header_to_height` and `headers_pool`:

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
``` [3](#0-2) 

**Failure at verification time**

After eviction, the first lookup in `verify_transaction_inclusion` panics:

```rust
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [4](#0-3) 

The same failure path exists in `verify_transaction_inclusion_v2`, which delegates to the v1 function after its coinbase check: [5](#0-4) 

**No re-submission path**

`submit_block_header` calls `get_prev_header`, which panics with `"PrevBlockNotFound"` if the predecessor is absent from `headers_pool`:

```rust
fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
    self.headers_pool
        .get(&current_header.prev_block_hash)
        .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
}
``` [6](#0-5) 

Because GC removes a contiguous range of the oldest blocks, their predecessors are also gone. The evicted block can never be re-submitted, making the failure permanent.

### Impact Explanation

Any NEAR smart contract ("recipient contract consuming verification results") that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to gate an irreversible action — releasing bridged assets, minting tokens, updating state — will be permanently blocked if the referenced block has been GC'd. The Bitcoin transaction is confirmed on-chain; the NEAR-side action can never complete. This is a direct analog to the USDC adapter scenario: the initiating event (Bitcoin confirmation / USDC burn) is irreversible, but the finalizing step (NEAR proof verification / USDC mint) permanently fails, locking the user's value with no recovery mechanism.

### Likelihood Explanation

`run_mainchain_gc` is called automatically inside every `submit_blocks` invocation:

```rust
self.run_mainchain_gc(num_of_headers);
``` [7](#0-6) 

The recommended `gc_threshold` is 52 704 blocks (~1 year). Any user whose downstream application delays proof submission beyond that window — due to application latency, network issues, or simply a slow cross-chain workflow — will hit this permanently broken state. The likelihood is medium: the window is large, but real cross-chain applications routinely experience delays measured in days to weeks, and a single year of Bitcoin blocks is not an unreasonably long horizon for a production deployment.

### Recommendation

Add an explicit check that the target block's height is at or above the current GC floor (`mainchain_initial_blockhash` height) before proceeding with proof verification. Return a clear, non-panicking error so callers can distinguish "block GC'd" from "block never existed":

```rust
let initial_height = self
    .headers_pool
    .get(&self.mainchain_initial_blockhash)
    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    .block_height;

require!(
    target_block_height >= initial_height,
    "Block has been garbage collected and can no longer be verified"
);
```

This gives downstream contracts a deterministic, recoverable error signal rather than a misleading panic.

### Proof of Concept

1. Initialize the contract with `gc_threshold = 10`.
2. Submit 25 blocks (heights 0–24); GC evicts heights 0–14, keeping 15–24.
3. Call `verify_transaction_inclusion` with `tx_block_blockhash` = hash of block at height 5, `confirmations = 5` (5 ≤ 10, passes the guard).
4. The call panics: `"block does not belong to the current main chain"` because height 5 was evicted.
5. Attempt to re-submit block 5 via `submit_blocks`; it panics with `"PrevBlockNotFound"` because block 4 is also gone.
6. The proof can never be verified; any downstream asset release depending on it is permanently locked.

### Citations

**File:** contract/src/lib.rs (L181-181)
```rust
        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L347-368)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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

**File:** contract/src/lib.rs (L671-675)
```rust
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
    }
```
