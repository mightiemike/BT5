### Title
Digishield Difficulty Computed from Canonical Mainchain Instead of Fork Ancestry — (`contract/src/dogecoin.rs`)

### Summary

`get_next_work_required` fetches the `height_first` ancestor block's timestamp via `get_header_by_height`, which unconditionally reads from `mainchain_height_to_header` — the canonical chain index — rather than walking the fork's own parent chain. A developer-acknowledged TODO comment in the code confirms this is a known open question. When a fork diverges at or before `height_first`, the contract uses the wrong timestamp for Digishield retarget computation, accepting a fork block whose `bits` field would be rejected by any correct Dogecoin node.

---

### Finding Description

In `get_next_work_required`, after the Digishield switch height (block ≥ 145 000, `difficulty_adjustment_interval = 1`), `blocks_to_go_back` is set to `1`, so `height_first = prev_block_header.block_height - 1`. [1](#0-0) 

The ancestor timestamp is then fetched with:

```rust
// TODO: check if it is correct to get block header by height from mainchain
//       without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← canonical chain only
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [3](#0-2) 

`mainchain_height_to_header` is a height→hash index that is only updated for the canonical chain: [4](#0-3) 

Fork headers are stored only in `headers_pool` (keyed by hash), with no height index: [5](#0-4) 

**Consequence:** When a fork diverges at height D and a fork block is submitted at height D+2, `height_first = D`. The contract reads the *mainchain* block at height D (timestamp `T_main`) instead of the fork's block at height D (timestamp `T_fork`). `calculate_next_work_required` then uses `T_main` to compute `modulated_timespan`, producing a `bits` value that differs from what the fork's own history requires. [6](#0-5) 

The `check_pow` guard enforces `expected_bits == block_header.bits`, but `expected_bits` is now computed from the wrong timestamp, so it validates the wrong target: [7](#0-6) 

---

### Impact Explanation

An attacker who mines a competing Dogecoin fork (no privileged role required — only hashpower) can choose timestamps on fork blocks at height D such that the contract computes a lower `expected_bits` (easier target) for blocks at D+2 and beyond. The attacker mines those blocks at the artificially easy target, the honest relayer submits them (it follows chainwork), and the contract accepts them. If the fork accumulates more chainwork than the mainchain, a chain reorg occurs and the contract's canonical chain pointer (`mainchain_tip_blockhash`) is updated to the attacker's fork. Downstream bridge logic that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against this canonical chain will treat attacker-controlled state as finalized Dogecoin state — a light client verification bypass. [8](#0-7) 

---

### Likelihood Explanation

The `#[trusted_relayer]` gate on `submit_blocks` does not block this attack: the attacker does not call `submit_blocks` directly. They mine valid-PoW Dogecoin headers on a competing fork; the honest relayer observes the fork on the Dogecoin P2P network and submits it. The difficulty manipulation reduces the hashpower required below a standard 51% threshold, making the attack cheaper than a conventional double-spend. The TODO comment in the production code confirms the developers themselves identified this as an unresolved correctness question. [9](#0-8) 

---

### Recommendation

Replace the `get_header_by_height` call with an ancestor walk that follows `prev_block_hash` links through `headers_pool` from `prev_block_header` back `blocks_to_go_back` steps. This mirrors what the reference Dogecoin node does (`GetAncestor`) and ensures the retarget timestamp is always taken from the fork's own history, not the canonical chain.

---

### Proof of Concept

1. Let the canonical mainchain have block M at height D with timestamp `T_main = 1_000_000`.
2. Attacker mines a fork block F at height D with timestamp `T_fork = 1_060_000` (60 000 s later), branching from the same parent as M.
3. Attacker mines fork block F' at D+1 (prev = F).
4. Attacker mines fork block F'' at D+2 (prev = F'). For F'', `height_first = D`.
5. Contract calls `get_header_by_height(D)` → returns M (mainchain), timestamp `T_main`.
6. `modulated_timespan` is computed from `T_prev(F') - T_main` instead of `T_prev(F') - T_fork`, yielding a larger timespan → lower difficulty → easier `bits`.
7. Attacker mines F'' meeting this easier `bits`; PoW check passes.
8. Honest relayer submits F, F', F'' (plus enough subsequent blocks to exceed mainchain chainwork).
9. Contract reorgs; attacker's fork becomes canonical.
10. `verify_transaction_inclusion` against a fabricated transaction in F'' returns `true`.

### Citations

**File:** contract/src/dogecoin.rs (L23-33)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let expected_bits =
            get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );
```

**File:** contract/src/dogecoin.rs (L286-297)
```rust
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

**File:** contract/src/dogecoin.rs (L301-333)
```rust
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
}
```

**File:** contract/src/lib.rs (L563-566)
```rust
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
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
