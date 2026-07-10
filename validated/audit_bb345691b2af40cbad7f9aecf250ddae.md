### Title
Displaced Mainchain Blocks Permanently Deleted from `headers_pool` During Chain Reorg — (File: `contract/src/lib.rs`)

---

### Summary

Inside `reorg_chain`, the helper `remove_block_header` is called on every mainchain block that is displaced by a winning fork. `remove_block_header` removes the block from **both** the mainchain index (`mainchain_header_to_height`) **and** the shared block pool (`headers_pool`). The correct action is to only remove the block from the mainchain index, leaving it in `headers_pool` as a fork block. Because the displaced blocks are erased from the pool, any subsequent attempt to submit new blocks that descend from the original chain panics immediately, and no automatic recovery path exists.

---

### Finding Description

`remove_block_header` is defined as:

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);          // ← erases the block entirely
}
``` [1](#0-0) 

It is called in two places inside `reorg_chain`:

**Place 1** — trimming mainchain blocks that are taller than the fork tip:

```rust
for height in (fork_tip_height + 1)..=last_main_chain_block_height {
    let current_main_chain_blockhash = self
        .mainchain_height_to_header.get(&height)...;
    self.remove_block_header(&current_main_chain_blockhash);   // ← deletes from pool
    self.mainchain_height_to_header.remove(&height);
}
``` [2](#0-1) 

**Place 2** — swapping fork blocks into mainchain positions occupied by old mainchain blocks:

```rust
if let Some(current_main_chain_blockhash) = main_chain_block {
    self.remove_block_header(&current_main_chain_blockhash);   // ← deletes from pool
}
``` [3](#0-2) 

After both calls, the displaced mainchain blocks no longer exist anywhere in contract state. They are not retained as fork blocks.

The block submission path always begins with:

```rust
let prev_block_header = self.get_prev_header(&header.clone().into());
``` [4](#0-3) 

which resolves exclusively through `headers_pool`:

```rust
fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
    self.headers_pool
        .get(&current_header.prev_block_hash)
        .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
}
``` [5](#0-4) 

Because the displaced blocks are gone from `headers_pool`, any block whose `prev_block_hash` points to a displaced block will panic with `PrevBlockNotFound`. The relayer cannot submit new blocks extending the original chain without first manually re-submitting every displaced block in order from the common ancestor — behaviour that no standard relayer implementation performs automatically.

The analog to the external report is exact: `remove_block_header` (which **destroys** the block) is called where a demote-only helper (which **only removes the mainchain-index entry**, keeping the block in `headers_pool`) should be called. The direction of the state transition is inverted relative to the intended semantics.

---

### Impact Explanation

After a successful reorg:

1. All displaced mainchain blocks are erased from `headers_pool`.
2. The relayer's normal operation — fetching the next Bitcoin block and calling `submit_blocks` — panics immediately because the parent of every new original-chain block is missing from the pool.
3. The contract's canonical chain is permanently locked to the attacker's fork unless an operator manually re-submits every displaced block in ascending height order, which is not part of any standard relayer flow.
4. `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will permanently reject SPV proofs for transactions that were confirmed on the original chain, since those blocks are no longer reachable.

The corrupted state is the `headers_pool` map and the `mainchain_tip_blockhash` pointer, both of which now reflect the attacker's fork exclusively.

---

### Likelihood Explanation

`submit_blocks` is a public, payable, permissionless entry point (gated only by the optional `trusted_relayer` stake mechanism, which can be bypassed via the `UnrestrictedSubmitBlocks` role or simply by staking). [6](#0-5) 

An attacker needs only to submit a sequence of headers whose cumulative `chain_work` exceeds the current mainchain tip's `chain_work`. For Dogecoin and Litecoin (lower absolute difficulty), this is achievable with modest hashpower or by pre-mining a private fork. For Bitcoin mainnet the bar is higher, but the impact once triggered is permanent and requires no further attacker action.

---

### Recommendation

Introduce a separate `demote_block_header` helper that removes the block only from the mainchain index, not from `headers_pool`:

```rust
fn demote_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    // headers_pool intentionally kept: block becomes a fork block
}
```

Replace both `remove_block_header` calls inside `reorg_chain` (lines 591 and 635) with `demote_block_header`. The existing `remove_block_header` (which also removes from `headers_pool`) should continue to be used only in `run_mainchain_gc`, where permanent pruning is the explicit intent. [7](#0-6) 

---

### Proof of Concept

```
Initial state:
  mainchain: genesis → A (height 1) → B (height 2)   [tip = B]

Step 1 — attacker submits fork with higher chainwork:
  submit_blocks([A', B', C'])
  where chain_work(C') > chain_work(B)

  reorg_chain() fires:
    • B is displaced at height 2 → remove_block_header(B) → B deleted from headers_pool
    • A is displaced at height 1 → remove_block_header(A) → A deleted from headers_pool
    • tip = C'

Step 2 — honest relayer submits next original-chain block C (child of B):
  submit_blocks([C])
  → get_prev_header(C) looks up B in headers_pool
  → B is gone → panic("PrevBlockNotFound")

Step 3 — contract is permanently stuck on the attacker's fork [A' → B' → C'].
  verify_transaction_inclusion for any tx in A or B returns false.
  No new original-chain block can be submitted without manual re-submission of A and B first.
```

The entry path is fully unprivileged: `submit_blocks` → `submit_block_header` → `submit_block_header_inner` → `reorg_chain` → `remove_block_header` (wrong call site). [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
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

**File:** contract/src/lib.rs (L502-502)
```rust
        let prev_block_header = self.get_prev_header(&header.clone().into());
```

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```

**File:** contract/src/lib.rs (L586-593)
```rust
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
```

**File:** contract/src/lib.rs (L634-636)
```rust
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }
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
