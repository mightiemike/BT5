### Title
Wrong Ancestor Used for Difficulty Retarget After Reorg — (`contract/src/dogecoin.rs`)

### Summary

`get_next_work_required` in `dogecoin.rs` calls `blocks_getter.get_header_by_height(height_first)` to obtain the timestamp of the interval-start block for difficulty calculation. The implementation of `get_header_by_height` always reads from `mainchain_height_to_header`, which maps to the **current canonical chain** block at that height. After a reorg, this mapping is overwritten with the fork block that replaced the original mainchain block. A second fork chain submitted after the reorg will therefore have its difficulty calculated against the wrong ancestor's timestamp, not its own chain's ancestor. The code itself acknowledges this with a `TODO` comment at the exact line.

### Finding Description

**Relevant code path:**

`get_header_by_height` in `lib.rs`: [1](#0-0) 

This always resolves height → hash via `mainchain_height_to_header`, which is the current canonical mapping.

`get_next_work_required` in `dogecoin.rs` (the acknowledged TODO): [2](#0-1) 

`reorg_chain` overwrites `mainchain_height_to_header` and **deletes** the displaced mainchain block from `headers_pool`: [3](#0-2) 

`check_target` is called **before** `submit_block_header_inner`, so the difficulty check reads the already-reorged `mainchain_height_to_header`: [4](#0-3) 

**Concrete exploit sequence (Dogecoin mainnet, height ≥ 145,000, `difficulty_adjustment_interval = 1`):**

For `new_difficulty_protocol`, `blocks_to_go_back = 1`, so `height_first = prev_block_height - 1`. [5](#0-4) 

1. **State**: mainchain is `genesis → M1(1) → … → M_H(H)`.
2. **Reorg trigger**: attacker (trusted relayer) submits Fork A: `genesis → F1(1) → … → F_H(H) → F_{H+1}(H+1)` with higher chainwork. `reorg_chain` runs: `mainchain_height_to_header[H]` is overwritten with `F_H`'s hash; `M_H` is deleted from `headers_pool`.
3. **Exploit submission**: attacker submits Fork B: `genesis → G1(1) → … → G_H(H) → G_{H+1}(H+1) → G_{H+2}(H+2)`.
   - When `G_{H+2}` is validated, `prev_block_header = G_{H+1}` (height `H+1`), `height_first = H`.
   - `get_header_by_height(H)` returns **`F_H`** (the reorged-in block), not `G_H` (the actual ancestor of `G_{H+2}`).
   - `calculate_next_work_required` receives `first_block_time = F_H.time` instead of `G_H.time`.

**Timestamp manipulation to lower difficulty:**

The attacker sets `G_H.time` significantly later than `F_H.time`. Then:
- Correct `actual_time_taken = G_{H+1}.time − G_H.time` → small → high difficulty
- Wrong `actual_time_taken = G_{H+1}.time − F_H.time` → large → low difficulty

In `calculate_next_work_required`: [6](#0-5) 

Larger `actual_time_taken` → larger `modulated_timespan` → larger `new_target` → lower `expected_bits` (easier target). The attacker sets `G_{H+2}.bits` to match this lower `expected_bits`, and the PoW check passes against the easier target: [7](#0-6) 

### Impact Explanation

A block with an incorrect (artificially lowered) `bits` value is accepted into `headers_pool`. The PoW check is performed against `block_header.bits` (attacker-controlled), not the correct difficulty. This means the attacker mines `G_{H+2}` against a weaker target than the protocol requires. If the attacker's fork accumulates sufficient chainwork (possible since each block requires less work), it becomes canonical via `reorg_chain`, and all downstream `verify_transaction_inclusion` calls operate on a chain that violated the difficulty invariant.

### Likelihood Explanation

- Entry point is `submit_blocks`, gated by `#[trusted_relayer]` — a valid attacker-reachable path per scope rules.
- The reorg precondition is self-satisfiable: the attacker triggers it with Fork A, then exploits it with Fork B in the same or subsequent `submit_blocks` calls.
- The TODO comment at line 291 confirms the developers identified this as an open correctness question.
- For Dogecoin mainnet (height ≥ 145,000), every block is a retarget boundary, so the vulnerable code path is exercised on every single block submission after any reorg.

### Recommendation

Replace the height-based lookup with an ancestor walk. Instead of `blocks_getter.get_header_by_height(height_first)`, traverse backwards from `prev_block_header` using `get_prev_header` for `blocks_to_go_back` steps. This mirrors how `get_prev_header` is already used in the `pow_allow_min_difficulty_blocks` branch and how Zcash's implementation traverses the chain: [8](#0-7) 

The same fix applies to the identical pattern in `litecoin.rs`: [9](#0-8) 

### Proof of Concept

```
State: Dogecoin mainnet, height >= 145_000, skip_pow_verification = true (for testability)

1. Init contract at height H (retarget boundary).
   Mainchain: genesis → M_{H-1}(H-1) → M_H(H)
   mainchain_height_to_header[H-1] = M_{H-1}.hash, time = T_M

2. Submit Fork A tip at height H+1 with chainwork > mainchain:
   genesis → F_{H-1}(H-1) → F_H(H) → F_{H+1}(H+1)
   → reorg_chain fires: mainchain_height_to_header[H-1] = F_{H-1}.hash, time = T_F
   → M_{H-1} is deleted from headers_pool

3. Submit Fork B:
   genesis → G_{H-1}(H-1, time=T_G where T_G >> T_F) → G_H(H) → G_{H+1}(H+1) → G_{H+2}(H+2)
   
   When G_{H+2} is validated:
   - height_first = H+1 - 1 = H
   - get_header_by_height(H) returns F_H (time = T_F), NOT G_H (time = T_G)
   - actual_time_taken = G_{H+1}.time - T_F  (large, because T_F < T_G)
   - expected_bits = lower difficulty than correct value

4. Assert: expected_bits computed by contract != expected_bits computed using G_H.time
   → Block G_{H+2} accepted with incorrect bits, violating difficulty invariant.
```

### Citations

**File:** contract/src/lib.rs (L626-636)
```rust
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

**File:** contract/src/dogecoin.rs (L149-154)
```rust
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
```

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

**File:** contract/src/dogecoin.rs (L280-289)
```rust
    let mut blocks_to_go_back = difficulty_adjustment_interval - 1;
    if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
        blocks_to_go_back = difficulty_adjustment_interval;
    }

    // Go back by what we want to be 14 days worth of blocks
    let height_first = prev_block_header
        .block_height
        .checked_sub(blocks_to_go_back)
        .unwrap_or_else(|| env::panic_str("Height underflow when calculating first block height"));
```

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
```

**File:** contract/src/dogecoin.rs (L307-318)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;

    let min_timespan = retarget_timespan - (retarget_timespan / 4);
    let max_timespan = retarget_timespan + (retarget_timespan / 2);

    if modulated_timespan < min_timespan {
        modulated_timespan = min_timespan;
    } else if modulated_timespan > max_timespan {
        modulated_timespan = max_timespan;
    }
```

**File:** contract/src/zcash.rs (L87-103)
```rust
    let mut current_header = prev_block_header.clone();
    let mut total_target = U256::ZERO;
    let mut median_time = [0u32; MEDIAN_TIME_SPAN];

    let prev_block_median_time_past = {
        for i in 0..usize::try_from(config.pow_averaging_window).unwrap() {
            if i < MEDIAN_TIME_SPAN {
                median_time[i] = current_header.block_header.time;
            }

            let (sum, overflow) =
                total_target.overflowing_add(target_from_bits(current_header.block_header.bits));
            require!(!overflow, "Addition of U256 values overflowed");
            total_target = sum;

            current_header = prev_block_getter.get_prev_header(&current_header.block_header);
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
