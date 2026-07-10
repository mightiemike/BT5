### Title
Unprivileged Caller Can Aggressively Prune Mainchain Block Headers via `run_mainchain_gc` — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating NEAR entry point on `BtcLightClient` that carries no caller restriction. Any unprivileged NEAR account can invoke it with an arbitrarily large `batch_size`, immediately deleting every GC-eligible mainchain block header in a single transaction. This permanently breaks SPV proof verification for all pruned blocks and can cause chain-reorg logic to panic, both of which are irreversible on-chain state corruptions.

---

### Finding Description

`submit_blocks` — the primary relayer entry point — is correctly guarded by `#[trusted_relayer]`, which enforces that only staked/approved relayers (or accounts holding `Role::DAO` / `Role::UnrestrictedSubmitBlocks`) may submit headers. [1](#0-0) 

Inside `submit_blocks`, GC is triggered automatically with `batch_size = num_of_headers` — the count of headers submitted in that single call, which is typically a small number (e.g., 1–100). [2](#0-1) 

`run_mainchain_gc` is also exposed as a standalone public method: [3](#0-2) 

The only attribute on this function is `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. This attribute controls whether the function is callable when the contract is **paused** — it does **not** restrict who may call it when the contract is in normal (unpaused) operation. There is no `#[trusted_relayer]`, no `#[private]`, and no role check gating unprivileged callers.

The GC logic removes all mainchain blocks between `mainchain_initial_blockhash` and `gc_threshold` blocks behind the tip, bounded by `batch_size`: [4](#0-3) 

When the relayer calls GC via `submit_blocks`, `batch_size` equals the small number of headers just submitted, so eligible blocks are pruned gradually over many calls. An attacker calling `run_mainchain_gc(u64::MAX)` directly removes **all** GC-eligible blocks in a single transaction — every mainchain block older than `gc_threshold` blocks from the current tip is deleted from `mainchain_height_to_header`, `mainchain_header_to_height`, and `headers_pool`, and `mainchain_initial_blockhash` is advanced to the new oldest block.

---

### Impact Explanation

**Permanent SPV proof breakage.** `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` check `mainchain_header_to_height` to confirm a block belongs to the main chain and to enforce confirmation depth. Once a block is GC'd, this lookup returns `None` and the call panics with `"block does not belong to the current main chain"`. Any downstream contract or user that had not yet submitted their SPV proof for a block in the pruned range loses the ability to do so permanently — the block data is gone and cannot be re-inserted. [5](#0-4) 

**Reorg failure.** As documented in `CLAUDE.md`, if mainchain blocks near a fork point have been GC'd, `reorg_chain` panics with `PrevBlockNotFound` when it cannot walk back to the common ancestor. An attacker who aggressively GCs immediately after a fork is submitted can cause the reorg to fail permanently, freezing the contract's canonical chain at a stale tip. [6](#0-5) 

The corruption is bounded by `gc_threshold` — blocks within that window of the tip are safe — but all blocks outside it are permanently deleted in one call rather than gradually, eliminating the window of time downstream consumers would otherwise have.

---

### Likelihood Explanation

The preconditions are zero: any NEAR account with enough gas can call `run_mainchain_gc(u64::MAX)` at any time the contract is unpaused. No staking, no role, no deposit is required. The call is cheap and the effect is immediate and irreversible.

---

### Recommendation

Add a caller restriction to `run_mainchain_gc` equivalent to the one on `submit_blocks`. The simplest fix is to add `#[trusted_relayer]` (or a dedicated role check such as `Role::UnrestrictedRunGC` or `Role::DAO`) so that only authorized relayers or governance accounts can trigger manual GC. The internal call from `submit_blocks` already passes through the trusted-relayer gate, so it would remain unaffected.

---

### Proof of Concept

1. Deploy the contract and initialize it with a `gc_threshold` of, say, 1000 blocks.
2. Have the legitimate relayer submit 1200 blocks (so 200 blocks are now GC-eligible).
3. From any unprivileged NEAR account (no role, no stake), call:
   ```
   run_mainchain_gc(18446744073709551615)  // u64::MAX
   ```
4. All 200 GC-eligible blocks are deleted in one transaction. `mainchain_initial_blockhash` advances by 200.
5. Any subsequent call to `verify_transaction_inclusion` for a transaction in those 200 blocks panics with `"block does not belong to the current main chain"` — permanently, with no recovery path. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

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

**File:** contract/src/lib.rs (L638-643)
```rust
            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }
```
