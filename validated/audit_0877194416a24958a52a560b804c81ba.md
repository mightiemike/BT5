The key code is now fully visible. Let me trace the exact bug path.

### Title
Digishield Retarget Uses Mainchain Ancestor Instead of Fork Ancestor for `height_first` Timestamp — (`contract/src/dogecoin.rs` + `contract/src/lib.rs`)

---

### Summary

In Digishield mode (post-145,000 blocks), `get_next_work_required` computes the expected difficulty for every block by fetching the timestamp of the block at `height_first` via `get_header_by_height`. That function unconditionally reads from `mainchain_height_to_header`, the canonical chain index. When the block being validated is on a fork that diverged before `height_first`, the mainchain block at that height is a **different block** with a **different timestamp** than the fork's true ancestor. The contract therefore computes the wrong expected `bits` for fork blocks, allowing a crafted fork to pass difficulty validation with a target that a correct Dogecoin node would reject.

The code itself acknowledges the problem with a TODO comment at the exact call site.

---

### Finding Description

**Digishield interval in post-145,000 mode** [1](#0-0) 

`difficulty_adjustment_interval` is set to `1` for every block above height 145,000. Because `(prev_height + 1) % 1 == 0` is always true, the retarget path is taken on **every single block**. `blocks_to_go_back` is then set to `1` (the full interval), so `height_first = prev_block_height - 1`.

**The wrong lookup** [2](#0-1) 

The TODO comment at line 291 is the developers' own acknowledgement of the bug. `get_header_by_height` is called with `height_first`, but: [3](#0-2) 

It reads exclusively from `mainchain_height_to_header`. There is no path through which it can return a fork block — it always returns the canonical chain block at that height.

**When the discrepancy matters**

Consider a fork that diverges at height `H-1`:

```
Mainchain: ... → [H-2] → [H-1_main] → [H_main] → ...
Fork:      ... → [H-2] → [H-1_fork] → [H_fork] → [H+1_fork]
```

When validating `H+1_fork`:
- `prev_block_header` = `H_fork`
- `height_first` = `H - 1`
- `get_header_by_height(H-1)` returns `H-1_main` (mainchain block)
- The fork's true ancestor at `H-1` is `H-1_fork` — a **different block with a different timestamp**

The timestamp fed into `calculate_next_work_required` is wrong: [4](#0-3) 

`modulated_timespan = prev_block_header.time - first_block_time`. If the attacker sets `H-1_fork.time` to be significantly later than `H-1_main.time`, the contract uses the earlier mainchain timestamp, producing a **larger** `modulated_timespan` and therefore a **lower difficulty (easier target)** than a correct Dogecoin node would compute.

**Validation of the fork blocks leading up to `H+1_fork`**

- `H-1_fork`: `height_first = H-3`. Fork diverges at `H-1`, so the fork's ancestor at `H-3` IS the mainchain block at `H-3`. Validation is correct.
- `H_fork`: `height_first = H-2`. Fork's ancestor at `H-2` IS the mainchain block at `H-2`. Validation is correct.
- `H+1_fork`: `height_first = H-1`. Fork's ancestor at `H-1` is `H-1_fork` ≠ `H-1_main`. **Validation is wrong.**

The attacker only needs to mine three fork blocks with valid PoW at the (correctly computed) difficulty for the first two, and then the third block (`H+1_fork`) is validated against an incorrectly computed, potentially easier target.

---

### Impact Explanation

The contract accepts a Dogecoin fork block whose `bits` field encodes a difficulty that a correct Dogecoin full node would reject. If this fork accumulates enough chainwork to trigger `reorg_chain`, the contract's canonical chain contains an invalid header. Downstream bridge logic that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against this chain treats non-Dogecoin-final state as trusted — a light client verification bypass. [5](#0-4) 

---

### Likelihood Explanation

On Dogecoin **testnet**, mining difficulty is low enough that an attacker can produce the required three fork blocks with valid PoW. The honest relayer observes the fork on the testnet P2P network and submits it via `submit_blocks`. The relayer does not independently validate difficulty — it submits whatever headers it sees. The `#[trusted_relayer]` gate controls *who* can call `submit_blocks`, not *what data* they submit; an honest relayer faithfully relays attacker-crafted testnet headers.

---

### Recommendation

Replace the `get_header_by_height` call in `get_next_work_required` with a walk up the fork's own ancestry using `get_prev_header`, starting from `prev_block_header` and stepping back `blocks_to_go_back` times. This is exactly what the reference Dogecoin implementation does and what the TODO comment is pointing toward. The same fix is needed in `contract/src/bitcoin.rs` and `contract/src/litecoin.rs` which share the same pattern. [6](#0-5) 

---

### Proof of Concept

1. Honest chain is at height `H` (H > 145,000). Mainchain block at `H-1` has timestamp `T_main`.
2. Attacker mines `H-1_fork` (parent = mainchain `H-2`) with timestamp `T_fork = T_main + 3600` (1 hour later, within the 2-hour future limit). Difficulty for `H-1_fork` is computed correctly (uses mainchain `H-3` timestamp). Attacker mines valid PoW.
3. Attacker mines `H_fork` (parent = `H-1_fork`). Difficulty computed correctly (uses mainchain `H-2` timestamp). Attacker mines valid PoW.
4. Attacker constructs `H+1_fork` (parent = `H_fork`). Contract computes `expected_bits` using `T_main` (mainchain `H-1` timestamp) instead of `T_fork`. The resulting `modulated_timespan` is `H_fork.time - T_main`, which is larger than the correct `H_fork.time - T_fork` by 3600 seconds. After Digishield damping (`retarget + (actual - retarget)/8`), this produces a measurably easier target. Attacker sets `H+1_fork.bits` to match this easier target and mines valid PoW against it.
5. Contract calls `check_pow` → `get_next_work_required` → `get_header_by_height(H-1)` → returns `H-1_main` → computes the same easier `expected_bits` → `require!(expected_bits == block_header.bits)` passes. Block is accepted.
6. A correct Dogecoin node would compute `expected_bits` using `T_fork`, producing a harder target, and would reject `H+1_fork`. [7](#0-6) [3](#0-2)

### Citations

**File:** contract/src/dogecoin.rs (L244-297)
```rust
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
