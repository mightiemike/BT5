After thoroughly reading the contract source, I found a concrete analog.

---

### Title
Fork Block Difficulty Validation Uses Stale Main-Chain Reference Block Instead of Fork-Chain Ancestor — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`)

### Summary

When validating the difficulty of a fork block, `get_next_work_required` resolves the reference block (used to compute the expected difficulty) via `get_header_by_height`, which reads exclusively from `mainchain_height_to_header`. For fork blocks whose chain diverges before the reference height, the main chain's block at that height is used instead of the fork chain's block. This desynchronization between the fork chain being validated and the main chain state used for difficulty calculation allows an adversary to submit fork blocks that satisfy an incorrectly computed (easier) difficulty target, bypassing the protocol's actual PoW requirement.

### Finding Description

**Vulnerability class**: Cross-module desynchronization — state is read from one module (main chain index) before the relevant state (fork chain) has been promoted, causing a calculation to operate on stale/wrong data.

**Analog mapping to the report**: In the original bug, `handleRainAndSops` reads global roots (which include unclaimed germinating roots) before `endAccountGermination` updates the user's local roots, causing the user's plenty share to be calculated from stale state. Here, `get_next_work_required` reads the main chain's block at a reference height before the fork chain's block at that height has been promoted to the main chain index, causing the difficulty calculation to use the wrong reference block.

**Concrete trigger path (Dogecoin, per-block Digishield, height ≥ 145,000):**

In `dogecoin.rs`, `get_next_work_required` computes the expected difficulty for every block using:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [1](#0-0) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [2](#0-1) 

This always returns the **main chain** block at the given height. Fork blocks are stored only in `headers_pool` via `store_fork_header`, never in `mainchain_height_to_header`:

```rust
fn store_fork_header(&mut self, header: &ExtendedHeader) {
    self.headers_pool.insert(&header.block_hash, header);
}
``` [3](#0-2) 

The difficulty check (`check_target`) is called **before** `submit_block_header_inner` classifies the block as fork or main chain: [4](#0-3) 

**Step-by-step desynchronization:**

Suppose the fork diverges at height K (fork block at height K is in `headers_pool` but not in `mainchain_height_to_header`).

- Fork block at height K+1: `height_first = K-1`. Since blocks 0…K-1 are shared, `get_header_by_height(K-1)` returns the correct block. ✓
- Fork block at height K+2: `height_first = K`. `get_header_by_height(K)` returns the **main chain's** block at height K, not the **fork's** block at height K. ✗

If the attacker crafts the fork's block at height K with a timestamp `T_fork_K` later than the main chain's `T_main_K`:

- Fork's actual `modulated_timespan` for block K+2 = `T_fork_K+1 − T_fork_K` (smaller, since `T_fork_K` is later)
- Check's `modulated_timespan` = `T_fork_K+1 − T_main_K` (larger, since `T_main_K` is earlier)
- Larger timespan → larger target → **easier difficulty**
- The contract accepts fork block K+2 at a lower difficulty than the protocol actually requires

The same issue exists in `bitcoin.rs` at every 2016-block difficulty boundary: [5](#0-4) 

The developer's own TODO comment in `dogecoin.rs` explicitly acknowledges this unresolved concern: [1](#0-0) 

### Impact Explanation

An adversary can submit fork blocks that satisfy an incorrectly computed (easier) difficulty target. The contract accepts these blocks. If the fork accumulates sufficient chainwork (which is easier to achieve since each block requires less PoW), `reorg_chain` is triggered, promoting the fork to the canonical main chain. This corrupts `mainchain_height_to_header`, `mainchain_header_to_height`, and `mainchain_tip_blockhash` — the state that all downstream consumers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` rely on for SPV proof correctness. A transaction that was never confirmed on the real Bitcoin/Dogecoin chain could be made to appear confirmed. [6](#0-5) 

### Likelihood Explanation

For **Dogecoin** (per-block Digishield after height 145,000): the desynchronization activates for any fork block at height K+2 or later, requiring only a 2-block-deep fork. Any unprivileged NEAR caller acting as a relayer can submit such headers via `submit_blocks`. The timestamp manipulation needed is bounded only by `MAX_FUTURE_BLOCK_TIME_LOCAL` (2 hours), which is a realistic window. Likelihood: **Medium-High**.

For **Bitcoin**: the desynchronization only activates at 2016-block difficulty boundaries, requiring a very long fork. Likelihood: **Low**.

### Recommendation

Replace `get_header_by_height` (main-chain-only lookup) with a chain-ancestor walk starting from `prev_block_header` when computing difficulty for fork blocks. The correct reference block must be found by traversing `prev_block_hash` links through `headers_pool` rather than by height index. This is exactly what the TODO comment in `dogecoin.rs` flags as unresolved.

### Proof of Concept

1. Deploy the contract with Dogecoin feature, initialized at height ≥ 145,001.
2. Submit a valid fork block `F_K` at height K, with `time = main_chain_block_K.time + 7000` (within the 2-hour future window).
3. Submit a valid fork block `F_K+1` at height K+1 (difficulty check uses `get_header_by_height(K-1)` = shared ancestor → correct, passes normally).
4. Craft fork block `F_K+2` at height K+2. The contract computes expected difficulty using `get_header_by_height(K)` = main chain block K (timestamp `T_main_K`), not fork block K (timestamp `T_main_K + 7000`). The computed `modulated_timespan` is inflated by 7000 seconds, yielding an easier target.
5. Mine `F_K+2` at this easier target. Submit via `submit_blocks`. The contract accepts it despite the block not meeting the actual Digishield difficulty for the fork chain.
6. Repeat for subsequent fork blocks. Once fork chainwork exceeds main chain chainwork, `reorg_chain` executes, corrupting the canonical chain state.

### Citations

**File:** contract/src/dogecoin.rs (L176-203)
```rust
        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
        }

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
```

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
```

**File:** contract/src/lib.rs (L531-567)
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
```

**File:** contract/src/lib.rs (L665-667)
```rust
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L78-86)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
