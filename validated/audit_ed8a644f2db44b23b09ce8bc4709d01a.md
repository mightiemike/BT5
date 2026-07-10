### Title
`run_mainchain_gc` removes blocks still within the confirmation window, causing permanent proof lockout — (`contract/src/lib.rs`)

---

### Summary

The `confirmations <= gc_threshold` guard in `verify_transaction_inclusion` is the sole protection ensuring a queried block is still in storage. It checks only the *requested* confirmation count, not whether the *specific block hash* is still present in `mainchain_header_to_height`. After `run_mainchain_gc` removes the oldest block, any call to `verify_transaction_inclusion` for that block panics with `"block does not belong to the current main chain"` — even when `confirmations <= gc_threshold` — permanently locking out proof verification for that block.

---

### Finding Description

**Exact state trace:**

Let `gc_threshold = T`. Suppose the chain holds exactly `T+1` blocks at heights `h` through `h+T`.

A trusted relayer calls `submit_blocks` with 1 header (height `h+T+1`). Inside `submit_blocks`, after appending the header, `run_mainchain_gc(1)` is called with `batch_size = 1`.

Inside `run_mainchain_gc`:

```
amount_of_headers_we_store = (h+T+1) − h + 1 = T+2
total_amount_to_remove     = T+2 − T = 2
selected_amount_to_remove  = min(2, 1) = 1
``` [1](#0-0) 

The loop runs for `height = h` only, calling `remove_block_header(blockhash_at_h)`: [2](#0-1) 

`remove_block_header` deletes the block from **both** `mainchain_header_to_height` and `headers_pool`: [3](#0-2) 

After GC, the chain holds `T+1` blocks at heights `h+1` through `h+T+1`. Block at height `h` is permanently gone.

**Now a proof consumer calls `verify_transaction_inclusion` for `blockhash_at_h` with `confirmations = T`:**

Step 1 — the guard passes:
```
require!(T <= T)  →  OK
``` [4](#0-3) 

Step 2 — lookup panics:
```rust
mainchain_header_to_height.get(&blockhash_at_h)  →  None
→ panic: "block does not belong to the current main chain"
``` [5](#0-4) 

The guard's own error message — *"The required number of confirmations exceeds the number of blocks stored in memory"* — explicitly implies that passing it guarantees the block is in memory. It does not. It only validates the *requested* confirmation count against the threshold, not whether the *specific queried block hash* is still present in storage.

---

### Impact Explanation

Once GC removes a block, it is irrecoverable. Any downstream contract or off-chain consumer that calls `verify_transaction_inclusion` (or `verify_transaction_inclusion_v2`, which delegates to it) for the removed block will permanently receive a panic revert. This is not a transient failure — the block hash is gone from `mainchain_header_to_height` and `headers_pool` forever. The impact is **permanent proof lockout** for the oldest block in the confirmation window, triggered systematically after every GC cycle. [6](#0-5) 

---

### Likelihood Explanation

GC runs automatically on every `submit_blocks` call with `batch_size = num_of_headers`. Under normal relayer operation (continuous block submission), GC fires on every call. Any proof consumer that attempts to verify a transaction in the block that was just pruned — a block that had `T+2` confirmations but was queried with `confirmations = T` — will hit this panic. The scenario requires no special privileges beyond the trusted relayer role, which is the normal operational path.

---

### Recommendation

Replace the `confirmations <= gc_threshold` guard with a direct storage presence check. After retrieving `target_block_height` from `mainchain_header_to_height`, verify that the block's height is at or above `mainchain_initial_blockhash`'s height (i.e., it has not been GC'd). Alternatively, change the guard to check that the queried block's actual confirmation depth does not exceed `gc_threshold`, rather than checking the caller-supplied `confirmations` argument.

---

### Proof of Concept

```rust
// 1. Init with gc_threshold = T, genesis at height h
// 2. Submit T blocks (chain: h..h+T, size = T+1)
// 3. Submit 1 block (height h+T+1) via submit_blocks([header])
//    → run_mainchain_gc(1) fires, removes block at height h
// 4. Call verify_transaction_inclusion for blockhash_at_h with confirmations = T
//    → guard passes (T <= T)
//    → panics: "block does not belong to the current main chain"
```

The `confirmations <= gc_threshold` check at line 290 passes, yet the call panics at line 300 because `remove_block_header` at line 660 already deleted `blockhash_at_h` from `mainchain_header_to_height`. This is directly testable with `skip_pow_verification = true` in a NEAR SDK unit test. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L169-181)
```rust
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L288-301)
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

**File:** contract/src/lib.rs (L388-393)
```rust
        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L401-409)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
```

**File:** contract/src/lib.rs (L659-661)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
```
