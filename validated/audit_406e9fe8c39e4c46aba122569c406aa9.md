### Title
GC-Induced `reorg_chain` Panic: Divergence-Point Erasure Breaks While-Loop Termination — (`contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` removes the divergence-point mainchain block from both `mainchain_header_to_height` **and** `headers_pool`. The `reorg_chain` while loop terminates only when it finds a block present in `mainchain_header_to_height`. After GC, the divergence-point block is absent from that map, so the loop does not stop there — it instead attempts to fetch that block from `headers_pool`, where it is also absent, triggering an unconditional `env::panic_str("previous fork block should be there")`.

---

### Finding Description

**GC removes blocks from both indexes simultaneously.**

`remove_block_header` is called for every block GC evicts:

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
``` [1](#0-0) 

`run_mainchain_gc` calls this for every height in the removal window: [2](#0-1) 

After GC, block H at the divergence height is gone from **both** `mainchain_header_to_height` and `headers_pool`.

**Fork blocks are stored only in `headers_pool`, never in `mainchain_header_to_height`.** [3](#0-2) 

So fork block F1 (whose `prev_block_hash` = H) is in `headers_pool` but H itself is not, after GC.

**The while loop's termination condition and its fetch target are now inconsistent.**

The loop terminates when the cursor block is found in `mainchain_header_to_height`: [4](#0-3) 

Since H was removed from `mainchain_header_to_height` by GC, the loop does **not** stop when the cursor reaches F1 (whose parent is H). It processes F1, then unconditionally fetches H from `headers_pool`: [5](#0-4) 

H is absent → `unwrap_or_else` fires → contract panics.

---

### Impact Explanation

The contract panics mid-execution. NEAR rolls back all state mutations from that transaction. The canonical state variables `mainchain_tip_blockhash` and `mainchain_initial_blockhash` are left in their pre-reorg state. The fork that legitimately accumulated more chainwork than the mainchain can never be promoted: every subsequent attempt to submit the fork tip re-triggers the same panic. The contract is permanently stuck with a stale canonical tip, and any downstream proof verification against the correct chain tip is blocked.

---

### Likelihood Explanation

The attack requires only a trusted relayer — the normal operational role for block submission, not a privileged admin. The sequence is:

1. Submit mainchain blocks until GC threshold is exceeded.
2. Submit fork block F1 anchored at block H (near the GC boundary) — succeeds because H is still in `headers_pool` at this point.
3. `run_mainchain_gc` (called automatically inside `submit_blocks` at line 181) removes H.
4. Submit additional fork blocks extending F1 — each succeeds because F1 is still in `headers_pool`.
5. Once fork chainwork exceeds mainchain chainwork, `reorg_chain` is triggered and panics. [6](#0-5) 

The GC call is automatic and unconditional on every `submit_blocks` invocation, so the window for step 3 is reliably reachable in normal operation.

---

### Recommendation

Before entering the while loop in `reorg_chain`, verify that the entire fork ancestor chain back to a live mainchain block is intact in `headers_pool`. Alternatively, change the loop termination condition to also check `headers_pool` presence, and return an error (rather than panic) if the divergence point has been GC'd. A more robust fix is to prevent GC from removing any block that is the parent of a known fork block — i.e., track fork-block parents and exclude them from the GC window.

---

### Proof of Concept

```
gc_threshold = 5

Step 1: submit mainchain blocks H0..H9 (10 blocks, 5 over threshold)
  → run_mainchain_gc removes H0..H4 from headers_pool and mainchain_header_to_height

Step 2: (before GC of H5) submit fork block F1 with prev_block_hash = H5
  → succeeds: H5 is still in headers_pool
  → F1 stored via store_fork_header (headers_pool only)

Step 3: submit mainchain block H10
  → run_mainchain_gc removes H5 from headers_pool and mainchain_header_to_height
  → F1 is still in headers_pool (GC ignores fork blocks)

Step 4: submit fork blocks F2..F6 extending F1
  → each succeeds: parent is in headers_pool
  → F6.chain_work > H10.chain_work → reorg_chain(F6, 10) triggered

Step 5: reorg_chain while loop
  cursor = F6 → not in mainchain_header_to_height → process, fetch F5
  cursor = F5 → not in mainchain_header_to_height → process, fetch F4
  ...
  cursor = F1 → not in mainchain_header_to_height → process, fetch H5
  headers_pool.get(H5) → None
  → env::panic_str("previous fork block should be there")  ← PANIC
``` [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L177-181)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L401-408)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
```

**File:** contract/src/lib.rs (L575-643)
```rust
    fn reorg_chain(&mut self, fork_tip_header: ExtendedHeader, last_main_chain_block_height: u64) {
        let fork_tip_height = fork_tip_header.block_height;
        if last_main_chain_block_height > fork_tip_height {
            // If we see that main chain is longer than fork we first garbage collect
            // outstanding main chain blocks:
            //
            //      [m1] - [m2] - [m3] - [m4] <- We should remove [m4]
            //     /
            // [m0]
            //     \
            //      [f1] - [f2] - [f3]
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
        }

        // Now we are in a situation where mainchain is equivalent to fork size:
        //
        //      [m1] - [m2] - [m3] - [m4] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip
        //
        //
        // Or in a situation where it is shorter:
        //
        //      [m1] - [m2] - [m3] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip

        let fork_tip_hash = fork_tip_header.block_hash.clone();
        let mut fork_header_cursor = fork_tip_header;

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
