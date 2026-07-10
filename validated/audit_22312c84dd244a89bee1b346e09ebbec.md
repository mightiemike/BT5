After thoroughly reading all production source files in the repository, I identified a concrete analog.

### Title
Fork Difficulty Calculation Uses Mainchain Ancestor Timestamps Instead of Fork Ancestor Timestamps - (`contract/src/dogecoin.rs`)

### Summary

In the Dogecoin (and Litecoin/Bitcoin) difficulty-adjustment logic, `get_next_work_required` resolves the difficulty-interval boundary block via `get_header_by_height`, which reads exclusively from `mainchain_height_to_header`. For fork blocks whose divergence point is within the current difficulty window, the fork's actual ancestor at that height is a **different block** than the mainchain block returned by the lookup. This desynchronization between the fork chain's ancestor state and the mainchain's height-to-header map mirrors the external report's root cause: a value that changes over time (ancestor timestamps driving difficulty) is read from a fixed snapshot (the mainchain map) instead of the live fork state.

### Finding Description

In `get_next_work_required` (Dogecoin path), after determining `height_first`, the code does:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
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

This always returns the **mainchain** block at `height_first`, never the fork's actual ancestor.

For Dogecoin after block 145,000, `difficulty_adjustment_interval` is set to `1`:

```rust
let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
let difficulty_adjustment_interval = if new_difficulty_protocol { 1 } else { config.difficulty_adjustment_interval };
``` [3](#0-2) 

With interval = 1, `blocks_to_go_back = 1` for all non-genesis blocks, so `height_first = prev_block_header.block_height - 1`.

**Concrete desynchronization trace** (fork diverges at height H):

| Fork block height | `height_first` | `get_header_by_height` returns | Fork's actual ancestor at that height | Correct? |
|---|---|---|---|---|
| H+1 | H−1 | mainchain block at H−1 | same block (pre-fork) | ✓ |
| H+2 | H | mainchain block at H | **fork's block at H** (different) | ✗ |
| H+3 | H+1 | mainchain block at H+1 | **fork's block at H+1** (different) | ✗ |

Starting at fork block H+2, every difficulty check uses the wrong ancestor timestamp. The developer explicitly acknowledged this with the `TODO` comment at line 291.

The same structural flaw exists in `litecoin.rs` and `bitcoin.rs`: [4](#0-3) [5](#0-4) 

For Bitcoin/Litecoin the interval is 2016 blocks, so the desynchronization only manifests when a fork diverges more than 2016 blocks deep — less likely but not impossible. For Dogecoin post-145,000 it manifests at every fork block beyond H+1.

### Impact Explanation

A relayer submitting adversarial chain data can exploit the desynchronization as follows:

1. The mainchain's block at height H has timestamp `T_main_H` (e.g., a slow block with an old timestamp).
2. The relayer submits a fork block at H with a recent timestamp `T_fork_H >> T_main_H`.
3. For fork block H+2, the expected difficulty is computed using `T_main_H` (mainchain's block at H), not `T_fork_H` (fork's block at H).
4. Because `T_main_H` is older, the computed time-span is larger, yielding a **lower expected difficulty** (higher target value, easier mining).
5. The relayer submits H+2 with lower `bits` (easier target); the `expected_bits == block_header.bits` check passes.
6. The relayer mines H+2 against the easier target, requiring less real PoW.
7. Repeating this across multiple fork blocks, the relayer accumulates chainwork faster than honest miners.
8. Once `current_header.chain_work > total_main_chain_chainwork`, `reorg_chain` is triggered. [6](#0-5) 

After the reorg, the mainchain contains blocks whose `bits` field was accepted against an incorrect difficulty target. Downstream callers of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` receive SPV proof results anchored to a chain whose PoW integrity is broken. [7](#0-6) 

### Likelihood Explanation

The entry path is a relayer submitting adversarial block data — explicitly listed as a supported production entrypoint in the audit scope. For Dogecoin post-145,000, the desynchronization activates for any fork that extends two or more blocks past its divergence point, which is the normal case for any meaningful fork. The attacker only needs to observe a mainchain block at height H with a timestamp favorable to lower difficulty (slow blocks occur naturally) and then submit a fork diverging at H. No leaked keys or social engineering are required beyond holding a relayer stake.

### Recommendation

Replace the `get_header_by_height` call with an ancestor traversal that follows `prev_block_hash` links through `headers_pool` starting from the fork's `prev_block_header`, walking back `blocks_to_go_back` steps. This ensures the difficulty boundary block is the fork's actual ancestor, not the mainchain's block at the same height:

```rust
// Instead of:
let first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time;

// Use ancestor traversal:
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This matches the reference Dogecoin implementation's intent of walking the actual ancestor chain rather than the mainchain index.

### Proof of Concept

```
Setup (Dogecoin mainnet, height > 145_000):
  Mainchain: ... → [H-1, T=1000] → [H, T=1060] → [H+1, T=1120] → tip

Attack:
  1. Relayer submits fork block at H:
       prev = mainchain block at H-1
       timestamp T_fork_H = 9999999  (far future, passes timestamp checks)
       bits = correct (difficulty computed from mainchain block at H-1, same as fork ancestor)
       → accepted, stored in headers_pool only

  2. Relayer submits fork block at H+1:
       prev = fork block at H
       difficulty computed using get_header_by_height(H-1) = mainchain block at H-1 (T=1000)
       actual_time = T_fork_H - 1000 = 8998999  → clamped to max → lower difficulty
       → fork block H+1 accepted with lower bits than honest chain requires

  3. Relayer mines H+1 against the easier target (less real PoW needed).

  4. Repeat for H+2, H+3, ... accumulating chainwork faster than honest miners.

  5. Once fork chainwork > mainchain chainwork → reorg_chain() fires.

  6. verify_transaction_inclusion() now operates on a chain with corrupted PoW integrity.
``` [8](#0-7) [9](#0-8)

### Citations

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

**File:** contract/src/dogecoin.rs (L300-332)
```rust
// source https://github.com/dogecoin/dogecoin/blob/2c513d0172e8bc86fe9a337693b26f2fdf68a013/src/dogecoin.cpp#L41
fn calculate_next_work_required(
    config: &DogecoinConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let retarget_timespan = config.pow_target_timespan;
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;

    let min_timespan = retarget_timespan - (retarget_timespan / 4);
    let max_timespan = retarget_timespan + (retarget_timespan / 2);

    if modulated_timespan < min_timespan {
        modulated_timespan = min_timespan;
    } else if modulated_timespan > max_timespan {
        modulated_timespan = max_timespan;
    }

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(modulated_timespan).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target =
        new_target / U256::from(<i64 as TryInto<u64>>::try_into(retarget_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
```

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

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
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

**File:** contract/src/litecoin.rs (L88-93)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
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
