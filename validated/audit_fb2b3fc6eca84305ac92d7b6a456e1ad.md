### Title
Missing Access Control on `run_mainchain_gc` Allows Any Caller to Corrupt Fork-Choice State — (`File: contract/src/lib.rs`)

---

### Summary

`BtcLightClient::run_mainchain_gc` is a public, state-mutating function with no caller access control in the unpaused state. Any unprivileged NEAR account can call it with an arbitrarily large `batch_size`, aggressively pruning mainchain block headers. Because chain reorganization requires walking back to a common ancestor that must still be in storage, an attacker can front-run a pending reorg by calling `run_mainchain_gc` first, permanently preventing the reorg from completing and locking the contract onto a weaker canonical chain.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

This decorator restricts the function only when the contract is **paused** — holders of `UnrestrictedRunGC` can bypass the pause. When the contract is running normally (unpaused), the decorator provides **zero caller restriction**. Any NEAR account, with no role whatsoever, can call `run_mainchain_gc(batch_size)` and mutate contract state.

By contrast, `submit_blocks` — the other state-mutating function — is protected by `#[trusted_relayer]`, which enforces that the caller must be a staked, active relayer or hold a bypass role: [2](#0-1) 

`run_mainchain_gc` has no equivalent guard.

The function removes the oldest mainchain block headers from `headers_pool` and `mainchain_height_to_header`, then advances `mainchain_initial_blockhash` forward: [3](#0-2) 

The amount removed is bounded by `min(total_amount_to_remove, batch_size)`, where `total_amount_to_remove = stored_count - gc_threshold`. Passing `u64::MAX` as `batch_size` causes the maximum possible pruning in a single call.

The chain reorganization logic in `reorg_chain` walks backward through `headers_pool` from the fork tip until it finds a block that is also in `mainchain_header_to_height` (the common ancestor): [4](#0-3) 

If the common ancestor block has been pruned by GC, `headers_pool.get(&prev_block_hash)` returns `None` and the contract panics with `"previous fork block should be there"`. The CLAUDE.md explicitly documents this invariant: [5](#0-4) 

---

### Impact Explanation

An attacker who calls `run_mainchain_gc(u64::MAX)` immediately before a reorg-triggering `submit_blocks` call prunes the common ancestor blocks. The subsequent reorg panics and reverts. The contract's canonical chain state — `mainchain_tip_blockhash`, `mainchain_height_to_header`, `mainchain_header_to_height` — permanently reflects the weaker chain. All downstream `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` calls then verify SPV proofs against the wrong canonical chain, returning incorrect inclusion results for any block that would have been on the true (higher-work) chain. [6](#0-5) 

This is fork-choice corruption: the canonical chain mapping is permanently corrupted without any cryptographic forgery.

---

### Likelihood Explanation

The entry path requires no privileges, no staking, and no special role. Any NEAR account can call `run_mainchain_gc`. The attacker only needs to monitor the NEAR blockchain for fork block submissions (which are public on-chain events) and submit a `run_mainchain_gc(u64::MAX)` transaction in the same or preceding block. NEAR's transaction ordering within a block is deterministic and observable, making front-running straightforward. The attack is repeatable: every time a reorg is attempted, the attacker can re-trigger GC to prevent it.

---

### Recommendation

Apply a role-based access control guard to `run_mainchain_gc` that restricts callers to trusted operators (e.g., `RelayerManager` or `DAO`) even when the contract is unpaused:

```rust
// Before:
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {

// After:
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::DAO, &env::predecessor_account_id())
            || self.acl_has_role(Role::RelayerManager, &env::predecessor_account_id()),
        "Caller is not authorized to run GC"
    );
    // ... rest of function
```

Alternatively, introduce a dedicated `GCManager` role analogous to `PauseManager`, and apply it symmetrically to both the paused and unpaused states.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704` and sync ~52800 mainchain headers (so the chain is just above threshold).
2. A fork with more chainwork than the current mainchain tip is submitted by the relayer — the fork's common ancestor is at height `mainchain_initial_blockhash + 1` (i.e., it would be pruned by one GC pass).
3. Attacker calls `run_mainchain_gc(u64::MAX)` — this removes `52800 - 52704 = 96` blocks, advancing `mainchain_initial_blockhash` past the common ancestor.
4. Relayer calls `submit_blocks([fork_tip])` — `reorg_chain` walks back from the fork tip, reaches the common ancestor's hash, calls `headers_pool.get(&prev_block_hash)`, gets `None`, and panics: `"previous fork block should be there"`.
5. The transaction reverts. The contract remains on the weaker chain. The attacker repeats step 3 on every subsequent reorg attempt. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L287-323)
```rust
    #[pause]
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

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
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

**File:** contract/src/lib.rs (L574-646)
```rust
    /// The most expensive operation which reorganizes the chain, based on fork weight
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

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```
