### Title
`verify_transaction_inclusion` Has No Snapshot Mechanism, Enabling Reorg-Based Proof Replay - (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2`) reads live mainchain state — `mainchain_tip_blockhash` and `mainchain_header_to_height` — without any checkpoint tied to a NEAR block height. Because `submit_blocks` can trigger a chain reorganization that mutates these mappings at any time, the same `(tx_id, tx_block_blockhash, merkle_proof)` tuple can oscillate between returning `true` and panicking/returning `false` across NEAR blocks. Consumer bridge contracts that rely on this result in asynchronous cross-contract callbacks, or that lack their own replay protection, are exposed to double-spend.

---

### Finding Description

`verify_transaction_inclusion` determines mainchain membership and confirmation depth entirely from live contract state:

```rust
let heaviest_block_header = self
    .headers_pool
    .get(&self.mainchain_tip_blockhash)          // live tip
    ...
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)               // live mainchain index
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [1](#0-0) 

Both `mainchain_tip_blockhash` and `mainchain_header_to_height` are mutable contract state. `submit_blocks` → `submit_block_header_inner` → `reorg_chain` rewrites both maps whenever a fork accumulates more chainwork than the current tip:

```rust
if current_header.chain_work > total_main_chain_chainwork {
    log!("Chain reorg");
    self.reorg_chain(current_header, last_main_chain_block_height);
}
``` [2](#0-1) 

During `reorg_chain`, old mainchain blocks are evicted from `mainchain_header_to_height` and replaced by fork blocks:

```rust
let main_chain_block = self
    .mainchain_height_to_header
    .insert(&current_height, &current_block_hash);
self.mainchain_header_to_height
    .insert(&current_block_hash, &current_height);

if let Some(current_main_chain_blockhash) = main_chain_block {
    self.remove_block_header(&current_main_chain_blockhash);
}
``` [3](#0-2) 

`remove_block_header` removes the old block from `mainchain_header_to_height`: [4](#0-3) 

There is no parameter analogous to `blockNumber` in the external report — no way to ask "was block B canonical at NEAR block height N?" The function always answers for the present moment.

`verify_transaction_inclusion_v2` delegates entirely to `verify_transaction_inclusion` after the coinbase check, so it inherits the same root cause: [5](#0-4) 

---

### Impact Explanation

A consumer bridge contract that calls `verify_transaction_inclusion` as an asynchronous cross-contract call receives a result computed at NEAR block N. Between block N and the block in which the callback executes, a relayer can call `submit_blocks` with a competing fork, triggering a reorg that removes the verified block from the mainchain. The callback still receives `true` (the result is frozen at call time), but the block is no longer canonical. If the bridge releases funds on that `true` result, and the attacker subsequently submits another fork that restores the original block to the mainchain, the same proof can be submitted again and will again return `true` — enabling double-spend. The light client itself records no history of which proofs have been verified, so it cannot prevent this replay.

---

### Likelihood Explanation

Any entity that can call `submit_blocks` (a registered trusted relayer, or an account with `Role::UnrestrictedSubmitBlocks` or `Role::DAO`) can trigger a reorg by submitting a pre-built fork chain with higher cumulative chainwork. For chains with lower hashrate (Dogecoin, Litecoin, Zcash testnets), constructing such a fork is inexpensive. Even on Bitcoin mainnet, a legitimate deep reorg submitted by an honest relayer is sufficient to trigger the TOCTOU window without any attacker involvement — the vulnerability is structural, not attacker-exclusive.

---

### Recommendation

Add a `verify_transaction_inclusion_at` variant that accepts a `near_block_height: u64` parameter and verifies that the block was canonical **at that specific NEAR block height**, analogous to the `getPriorVotingPower(blockNumber)` recommendation in the external report. This requires the contract to checkpoint mainchain state (e.g., recording the canonical block hash at each height per NEAR block). At minimum, document explicitly that the returned boolean is only valid for the NEAR block in which the call is processed, and that consumer contracts must implement their own replay protection keyed on `(tx_id, tx_block_blockhash)` before acting on the result.

---

### Proof of Concept

1. Block B (containing attacker's Bitcoin transaction TX) is on the mainchain. `mainchain_header_to_height[B] = h`.
2. Consumer bridge contract calls `verify_transaction_inclusion({tx_id: TX, tx_block_blockhash: B, confirmations: 6})` as a cross-contract call. The call is processed in NEAR block N; the function reads `mainchain_tip_blockhash` and `mainchain_header_to_height[B]`, returns `true`.
3. Before the callback executes, attacker submits a fork via `submit_blocks` in NEAR block N+1. The fork has higher chainwork; `reorg_chain` runs, removing B from `mainchain_header_to_height` and updating `mainchain_tip_blockhash`.
4. The callback in NEAR block N+2 receives `true` (frozen from step 2). Bridge releases funds.
5. Attacker submits another fork in NEAR block N+3 that restores B to the mainchain. `mainchain_header_to_height[B] = h` again.
6. Attacker calls the bridge again with the identical `ProofArgs`. Bridge calls `verify_transaction_inclusion` again; it reads current live state, finds B on the mainchain with sufficient confirmations, returns `true`.
7. Bridge releases funds a second time. Double-spend complete. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L288-323)
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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L575-647)
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

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
    }
```

**File:** contract/src/lib.rs (L659-661)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
```
