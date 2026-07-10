### Title
Stale Mainchain State Used in Fork Difficulty Calculation — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

All three chain-specific `get_next_work_required` implementations look up the difficulty-period reference block via `get_header_by_height`, which reads exclusively from `mainchain_height_to_header`. When the function is called during fork-block validation, the fork's actual ancestor at that height is stored only in `headers_pool` and is invisible to `get_header_by_height`. The function therefore silently substitutes the mainchain block's timestamp, producing an incorrect expected-bits value. An unprivileged proof submitter can exploit the resulting difficulty miscalculation to mine fork blocks at a lower-than-required target and trigger a chain reorg.

---

### Finding Description

`get_header_by_height` is implemented as a lookup into `mainchain_height_to_header`: [1](#0-0) 

This map is populated only by `store_block_header` (mainchain path) and updated by `reorg_chain`. Fork blocks are stored exclusively in `headers_pool` via `store_fork_header`: [2](#0-1) 

During fork-block submission, `submit_block_header` calls `check_target` → `check_pow` → `get_next_work_required`. At a difficulty-adjustment boundary, all three implementations call `get_header_by_height` to obtain the reference block's timestamp:

**Dogecoin** (with an explicit TODO acknowledging the problem): [3](#0-2) 

**Bitcoin:** [4](#0-3) 

**Litecoin:** [5](#0-4) 

When the fork diverges before `height_first`, the block at that height in `mainchain_height_to_header` is a mainchain block, not the fork's ancestor. The timestamp difference between the two blocks corrupts the `actual_time_taken` / `modulated_timespan` computation, producing an `expected_bits` value that does not correspond to the correct difficulty for the fork chain.

The broken invariant: **the difficulty check for a fork block must use the fork's own ancestor chain, not the current mainchain state.**

---

### Impact Explanation

An attacker who controls the timestamps of submitted fork headers can widen the apparent time span seen by `calculate_next_work_required` by ensuring the mainchain block at `height_first` has an earlier timestamp than the fork's actual ancestor at that height. A wider time span yields a lower difficulty (easier target). The attacker then mines fork blocks against the artificially easy target, accumulates chain work, and triggers `reorg_chain` once the fork's `chain_work` exceeds the mainchain tip's value: [6](#0-5) 

The result is a canonical-chain substitution backed by less real proof-of-work than the protocol requires, corrupting the fork-choice decision and invalidating the SPV guarantees offered to downstream `verify_transaction_inclusion` callers.

---

### Likelihood Explanation

**Dogecoin (high likelihood):** After block 145,000 the difficulty adjustment interval is 1, meaning `height_first = prev_block_height − 1`. [7](#0-6) 

The fork only needs to be **two blocks long** for the bug to manifest. Any unprivileged NEAR account can call `submit_blocks` with a crafted two-block fork sequence; no privileged role or leaked key is required.

**Bitcoin / Litecoin (lower likelihood):** The adjustment interval is 2016 blocks, so the fork must span a retarget boundary — a much longer sequence — before the stale lookup is reached. [8](#0-7) 

---

### Recommendation

Replace `get_header_by_height` (mainchain-only lookup) with a backward traversal of the fork chain starting from `prev_block_header`, walking `get_prev_header` until the target height is reached. This mirrors how Bitcoin Core resolves the ancestor: by following `prev_block_hash` links rather than a height index. Alternatively, maintain a separate height-indexed map per fork branch, or pass the ancestor explicitly through the call chain.

---

### Proof of Concept

**Dogecoin scenario (every-block retarget, height > 145,000):**

1. Mainchain tip is at height H. Mainchain block at H−1 has timestamp `T_main`.
2. Attacker calls `submit_blocks` with fork block `F_H` (parent = mainchain block at H−1, timestamp = `T_main + Δ` where Δ is chosen to be large but within the `time-too-new` bound).
3. Attacker calls `submit_blocks` with fork block `F_{H+1}` (parent = `F_H`, timestamp = `T_main + Δ + δ`).
4. During validation of `F_{H+1}`, `get_next_work_required` computes `height_first = H − 1` and calls `get_header_by_height(H − 1)`, returning the **mainchain** block at H−1 with timestamp `T_main`.
5. `modulated_timespan = (T_main + Δ + δ) − T_main = Δ + δ`. The correct value would be `δ` (time between `F_H` and `F_{H+1}`).
6. The inflated timespan produces a lower `expected_bits` (easier target). The attacker mines `F_{H+1}` against this easier target.
7. Repeating steps 3–6 for subsequent fork blocks, the attacker accumulates chain work and eventually triggers `reorg_chain`, replacing the canonical chain with a fork backed by insufficient real PoW. [9](#0-8) [10](#0-9)

### Citations

**File:** contract/src/lib.rs (L537-548)
```rust
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
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
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

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
```

**File:** contract/src/dogecoin.rs (L300-297)
```rust

```

**File:** contract/src/bitcoin.rs (L56-76)
```rust
    if (prev_block_header.block_height + 1) % config.difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            if block_header.time
                > prev_block_header.block_header.time + 2 * config.pow_target_spacing
            {
                return config.proof_of_work_limit_bits;
            }

            let mut current_block_header = prev_block_header.clone();
            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            let last_bits = current_block_header.block_header.bits;
            return last_bits;
        }
        return prev_block_header.block_header.bits;
    }
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

**File:** contract/src/litecoin.rs (L88-93)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
