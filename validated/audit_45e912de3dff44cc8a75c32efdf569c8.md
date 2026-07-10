### Title
Difficulty Calculation for Fork Blocks Uses Stale Mainchain State Instead of Fork Ancestor — (File: `contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`)

---

### Summary

Both `get_next_work_required` implementations (Dogecoin and Bitcoin) call `blocks_getter.get_header_by_height(height_first)` to obtain the reference block for difficulty retargeting. This helper always resolves through `mainchain_height_to_header`, the canonical-chain index. When the block being validated belongs to a fork, the mainchain index has not been updated to reflect the fork's ancestry, so the difficulty calculation silently uses a stale mainchain block instead of the fork's true ancestor at that height. This is the direct analog of the Dahlia bug: a required state synchronisation step is absent before a critical dependent calculation.

---

### Finding Description

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always the canonical index
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

In `dogecoin.rs`, `get_next_work_required` calls this function to obtain the first block of the retarget window, and the codebase itself flags the problem with a TODO:

```rust
// TODO: check if it is correct to get block header by height from mainchain
//       without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

The identical pattern appears in `bitcoin.rs`:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [3](#0-2) 

The call site is `check_target` → `check_pow` → `get_next_work_required`, invoked inside `submit_block_header` **before** `submit_block_header_inner` stores the fork block or triggers any reorg. At that moment `mainchain_height_to_header` still maps every height to the old canonical chain. If the fork diverged before `height_first`, the mainchain block at that height is a different block (with a potentially different timestamp) than the fork's true ancestor. [4](#0-3) 

The retarget formula (`calculate_next_work_required`) is sensitive to the timestamp of that reference block:

```rust
let modulated_timespan =
    i64::from(prev_block_header.block_header.time) - first_block_time;
``` [5](#0-4) 

A different `first_block_time` produces a different `expected_bits`, which is then compared against the submitted block's `bits` field. The PoW hash check is subsequently performed against `target_from_bits(header.bits)`, i.e., the attacker-supplied `bits` value that was just accepted.

---

### Impact Explanation

**Broken invariant:** Every accepted block must satisfy the difficulty computed from its own chain's ancestry. When a fork block is accepted with a difficulty derived from the mainchain's reference block, this invariant is violated.

**Concrete corrupted value:** `expected_bits` in `check_pow` is wrong for any fork block whose retarget window contains a height at which the fork and the mainchain diverge. If the mainchain's reference block has a *later* timestamp than the fork's reference block, `actual_timespan` is smaller, `expected_bits` encodes an *easier* target, and the attacker's fork block needs less real PoW to satisfy both the bits-equality check and the hash-below-target check.

**Downstream impact:** `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` resolve transaction membership against `mainchain_header_to_height`. If a fork chain with insufficient real work is promoted to the canonical chain via `reorg_chain`, every subsequent SPV proof call operates against a fraudulent chain, allowing a transaction that never occurred on the real Bitcoin network to be verified as included. [6](#0-5) 

---

### Likelihood Explanation

`submit_blocks` is gated by `#[trusted_relayer]`, so the direct trigger requires a trusted relayer (or an account holding `Role::UnrestrictedSubmitBlocks`) to supply adversarial headers. The report's scope explicitly includes "relayer-path user supplying adversarial chain data" as a valid entry point. The Dogecoin build is the most exposed: after block 145 000 the difficulty adjustment interval is 1, meaning `height_first = prev_block_height − 1`, so any fork that is at least two blocks deep already has a divergent reference block. The Bitcoin build requires a fork that diverges before the start of the current 2 016-block window, which is a longer setup but not infeasible for a relayer who controls the submission stream.

---

### Recommendation

Replace the `get_header_by_height` call inside `get_next_work_required` with an ancestor traversal that follows `prev_block_hash` links from `prev_block_header` backward by `blocks_to_go_back` steps. This mirrors how Bitcoin Core resolves the retarget ancestor and ensures the correct fork-local block is used regardless of the current canonical-chain state.

---

### Proof of Concept

**Dogecoin (post-145 000, interval = 1):**

1. Mainchain tip is at height H. Mainchain block at H−1 has `time = T_main`.
2. Attacker submits a fork block at height H−1 (diverging from the mainchain at H−2) with `time = T_fork`, where `T_fork > T_main` (later timestamp). This fork block is stored via `store_fork_header`.
3. Attacker submits a fork block at height H whose `prev_block_hash` points to the fork block at H−1.
4. `check_pow` calls `get_next_work_required`. `height_first = (H−1) − 1 = H−2`. `get_header_by_height(H−2)` returns the **mainchain** block at H−2 with time `T_ref_main`.
5. `actual_timespan = T_fork − T_ref_main`. Because `T_fork > T_main`, this timespan may be larger or smaller than the correct fork-based timespan `T_fork − T_ref_fork`, depending on `T_ref_fork` vs `T_ref_main`.
6. The attacker chooses `T_fork` such that the mainchain-derived `expected_bits` encodes an easier target than the fork-correct value. They set the fork block's `bits` to this easier value and find a PoW solution against the easier target.
7. Both `require!(expected_bits == block_header.bits)` and the hash-below-target check pass.
8. The fork block is accepted with less real work than required. Repeating this across multiple blocks accumulates enough `chain_work` (each block contributing `work_from_bits(easier_bits)`) to eventually exceed the mainchain's `chain_work`, triggering `reorg_chain` and corrupting the canonical chain. [7](#0-6) [8](#0-7)

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

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

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
    }
```

**File:** contract/src/dogecoin.rs (L229-297)
```rust
fn get_next_work_required(
    config: &DogecoinConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
    // Dogecoin: Special rules for minimum difficulty blocks with Digishield
    if allow_min_difficulty_for_block(config, block_header, prev_block_header) {
        // Special difficulty rule for testnet:
        // If the new block's timestamp is more than 2* nTargetSpacing minutes
        // then allow mining of a min-difficulty block.
        return config.proof_of_work_limit_bits;
    }

    // Only change once per difficulty adjustment interval
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };

    if (prev_block_header.block_height + 1) % difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            // Special difficulty rule for testnet:
            // If the new block's timestamp is more than 2* 10 minutes
            // then allow mining of a min-difficulty block.
            if block_header.time
                > prev_block_header.block_header.time + config.pow_target_spacing * 2
            {
                return config.proof_of_work_limit_bits;
            }

            // Return the last non-special-min-difficulty-rules-block
            let mut current_block_header = prev_block_header.clone();

            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            return current_block_header.block_header.bits;
        }

        return prev_block_header.block_header.bits;
    }

    // Litecoin: This fixes an issue where a 51% attack can change difficulty at will.
    // Go back the full period unless it's the first retarget after genesis. Code courtesy of Art Forz
    let mut blocks_to_go_back = difficulty_adjustment_interval - 1;
    if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
        blocks_to_go_back = difficulty_adjustment_interval;
    }

    // Go back by what we want to be 14 days worth of blocks
    let height_first = prev_block_header
        .block_height
        .checked_sub(blocks_to_go_back)
        .unwrap_or_else(|| env::panic_str("Height underflow when calculating first block height"));

    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
```

**File:** contract/src/bitcoin.rs (L81-86)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
