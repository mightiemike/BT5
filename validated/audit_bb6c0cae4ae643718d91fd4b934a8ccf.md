### Title
Unprivileged Caller Can Trigger `run_mainchain_gc` to Prematurely Prune Mainchain Headers - (File: `contract/src/lib.rs`)

### Summary
`run_mainchain_gc` is a public, state-mutating function that permanently deletes mainchain block headers from contract storage. Unlike `submit_blocks`, which is gated by `#[trusted_relayer]`, `run_mainchain_gc` carries only a `#[pause]` guard — meaning any unprivileged NEAR account can call it freely when the contract is not paused. This is the direct analog of the reported `burnFrom` role-bypass: a state-destructive operation that should be restricted to authorized callers is reachable by anyone.

### Finding Description
`submit_blocks` is the privileged entry point for relayers and is protected by both `#[pause]` and `#[trusted_relayer]`: [1](#0-0) 

Internally, `submit_blocks` calls `self.run_mainchain_gc(num_of_headers)` to prune old headers in a controlled, bounded batch. [2](#0-1) 

However, `run_mainchain_gc` is also exposed as a standalone public method with only a `#[pause]` guard: [3](#0-2) 

The `#[pause(except(roles(Role::UnrestrictedRunGC)))]` attribute only restricts callers when the contract is **paused** (the `UnrestrictedRunGC` role bypasses the pause). When the contract is **not paused**, there is no role check at all — any NEAR account can call `run_mainchain_gc` with an arbitrary `batch_size`.

The GC logic removes the oldest mainchain headers from `headers_pool` and `mainchain_height_to_header`, then advances `mainchain_initial_blockhash`: [4](#0-3) 

### Impact Explanation
An unprivileged caller can supply `batch_size = u64::MAX` to remove all headers that exceed `gc_threshold` in a single transaction. The removed headers are permanently gone from `headers_pool` and `mainchain_header_to_height`. This has two concrete consequences:

1. **Fork resolution failure**: `reorg_chain` walks back through the fork chain looking for the common ancestor by checking `mainchain_header_to_height`. If the common ancestor has been pruned by aggressive GC, the loop cannot terminate correctly and panics with `"previous fork block should be there"`. The contract's core invariant — "mainchain always has the highest cumulative chainwork" — is broken: a higher-work fork can never be promoted. [5](#0-4) 

2. **`verify_transaction_inclusion` failure**: SPV proofs for blocks that have been prematurely GC'd will panic with `ERR_KEY_NOT_EXIST`, breaking downstream consumers that rely on the light client for transaction verification. [6](#0-5) 

The project documentation explicitly acknowledges this risk: *"If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound`."*

### Likelihood Explanation
Likelihood is **low**. For the fork-resolution impact to materialize, the common ancestor of a competing fork must lie more than `gc_threshold` blocks behind the tip — practically impossible for Bitcoin (forks resolve in 1–2 blocks; `gc_threshold` is ~52,704). For the `verify_transaction_inclusion` impact, the attacker must race the relayer to prune blocks before a proof is submitted. The attack requires no privileged access and costs only gas, but the timing window is narrow and the bounded GC limits the blast radius to `amount_of_headers_we_store - gc_threshold` blocks.

### Recommendation
Add a role restriction to `run_mainchain_gc` so that only trusted relayers or a designated role can call it directly, mirroring the protection on `submit_blocks`:

```rust
// Before:
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }

// After:
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[trusted_relayer]   // or a dedicated GCManager role
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }
```

Alternatively, make the public entry point view-only and keep the mutable version private, exposing GC only through `submit_blocks`.

### Proof of Concept
1. Deploy the contract with `gc_threshold = 100` and submit 200 mainchain blocks via a trusted relayer account. The contract now holds 100 excess blocks.
2. From any unprivileged NEAR account (no role granted), call:
   ```json
   { "method": "run_mainchain_gc", "args": { "batch_size": 18446744073709551615 } }
   ```
3. The call succeeds. All 100 excess blocks are removed from `headers_pool` and `mainchain_height_to_header` in a single transaction. `mainchain_initial_blockhash` is advanced by 100 blocks.
4. Any subsequent `verify_transaction_inclusion` call referencing one of the pruned blocks panics with `ERR_KEY_NOT_EXIST`, confirming unauthorized state mutation by an unprivileged caller. [7](#0-6)

### Citations

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

**File:** contract/src/lib.rs (L310-313)
```rust
        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
```

**File:** contract/src/lib.rs (L371-416)
```rust
    /// Public call to run GC on a mainchain.
    /// `batch_size` is how many block headers should be removed in the execution
    ///
    /// # Panics
    /// If initial blockheader or tip blockheader are not in a header pool
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

**File:** contract/src/lib.rs (L616-642)
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
```
