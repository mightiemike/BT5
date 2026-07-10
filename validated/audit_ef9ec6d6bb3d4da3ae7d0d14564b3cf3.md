### Title
GC-Evicted Blocks Permanently Break Transaction Inclusion Proofs, Locking Downstream Bridge Funds - (File: `contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions unconditionally panic when queried for a block that has been removed by `run_mainchain_gc`. The guard `args.confirmations <= self.gc_threshold` is a false safety net: it checks only the confirmation count, not whether the specific target block is still in on-chain storage. Any downstream NEAR bridge contract that gates fund release on a successful proof call will have those funds permanently locked once the target block is evicted.

---

### Finding Description

`run_mainchain_gc` is invoked automatically inside every `submit_blocks` call:

```rust
// contract/src/lib.rs  line 181
self.run_mainchain_gc(num_of_headers);
```

When the stored block count exceeds `gc_threshold`, it removes the oldest blocks from **all three** storage structures:

```rust
// contract/src/lib.rs  lines 401-408
for height in start_removal_height..end_removal_height {
    let blockhash = &self
        .mainchain_height_to_header
        .get(&height)
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

    self.remove_block_header(blockhash);          // removes from headers_pool AND mainchain_header_to_height
    self.mainchain_height_to_header.remove(&height);
}
```

`remove_block_header` wipes the entry from both `mainchain_header_to_height` and `headers_pool`:

```rust
// contract/src/lib.rs  lines 659-662
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
```

`verify_transaction_inclusion` contains a guard that appears to protect callers:

```rust
// contract/src/lib.rs  lines 289-292
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

This guard is **insufficient**. It only validates that the requested confirmation count is within the GC window; it does not verify that the specific block hash supplied by the caller is still present in storage. Immediately after this guard, the function performs an unconditional panic-on-miss lookup:

```rust
// contract/src/lib.rs  lines 298-301
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

If the block has been GC-evicted, this panics with a misleading message ("block does not belong to the current main chain") even though the block was once a valid mainchain member. The same failure path exists in `verify_transaction_inclusion_v2`, which first panics on `headers_pool` lookup and then delegates to `verify_transaction_inclusion`:

```rust
// contract/src/lib.rs  lines 353-356
let header = self
    .headers_pool
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
```

Additionally, `run_mainchain_gc` is a public, permissionless entry point (callable by any account when the contract is not paused):

```rust
// contract/src/lib.rs  line 376-377
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

An adversary can call it directly with a large `batch_size` to accelerate eviction of any block that is already outside the `gc_threshold` window, racing against a user who is still accumulating confirmations.

---

### Impact Explanation

The BTC light client's stated purpose is to let NEAR contracts verify Bitcoin transaction inclusion. A bridge contract that gates fund release on a `verify_transaction_inclusion` call will receive a NEAR runtime panic (not a `false` return) when the target block is evicted. A panicking cross-contract call causes the entire bridge transaction to revert. Because the block is permanently gone from storage, every future attempt also panics. The user's funds are locked in the bridge contract with no recovery path — an exact structural match to the original report's "funds locked for the entire staking period" impact.

**Severity**: Medium
**Scope**: Any downstream NEAR contract consuming `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` as a gate for irreversible state changes (fund release, NFT mint, etc.).

---

### Likelihood Explanation

**Likelihood**: Medium

The default recommended `gc_threshold` is 52 704 blocks (~1 year of Bitcoin blocks), giving users a long window. However:

1. Operators may deploy with a smaller `gc_threshold` (the parameter is set freely at `init` time with no lower-bound enforcement).
2. `run_mainchain_gc` is publicly callable; any account can call it with `batch_size = u64::MAX` to immediately evict all blocks outside the current window, racing against a user who is still waiting for confirmations.
3. The misleading guard `confirmations <= gc_threshold` gives integrators a false guarantee, making it likely that bridge contracts will be written without an independent staleness check.
4. There is no event or return value from `run_mainchain_gc` that would alert a waiting user that their target block has been evicted.

---

### Recommendation

1. **Return `false` instead of panicking** when the target block is absent from `mainchain_header_to_height` or `headers_pool`. This converts a fund-locking panic into a recoverable `false` result that bridge contracts can handle gracefully.
2. **Add an explicit staleness check** before the lookup: verify that `current_tip_height - target_block_height < gc_threshold` and return `false` (not panic) if the block is outside the live window.
3. **Restrict `run_mainchain_gc`** to privileged roles or remove it as a standalone public entry point, since its only legitimate caller is `submit_blocks`.
4. **Document the GC eviction window** prominently in the API so bridge contract authors know they must submit proofs within `gc_threshold` blocks of the target block's inclusion.

---

### Proof of Concept

```
1. Deploy contract with gc_threshold = 200.

2. Submit 201 headers (heights 0–200).
   → run_mainchain_gc removes height 0 (block hash B0).

3. Call verify_transaction_inclusion with:
     tx_block_blockhash = B0   (the evicted block)
     confirmations      = 6    (well within gc_threshold = 200)

4. Guard passes:  6 <= 200  ✓

5. Lookup panics: mainchain_header_to_height.get(B0) → None
   → env::panic_str("block does not belong to the current main chain")

6. Any bridge contract awaiting this proof to release funds
   receives a panicking cross-contract call on every future attempt.
   Funds are permanently locked.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L179-181)
```rust
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L288-302)
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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
