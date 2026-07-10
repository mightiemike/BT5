### Title
Difficulty Retarget Uses Mainchain Block Timestamp Instead of Fork Ancestor Timestamp, Enabling Incorrect PoW Acceptance - (`contract/src/dogecoin.rs`, `contract/src/litecoin.rs`, `contract/src/bitcoin.rs`)

---

### Summary

In `get_next_work_required` for Dogecoin, Litecoin, and Bitcoin, when a fork block falls on a difficulty-adjustment boundary, the code fetches the "first block" of the retarget interval via `get_header_by_height`, which always returns the **mainchain** block at that height rather than the fork's actual ancestor. This is the same bug class as M-03: a wrong value is used in a critical calculation, producing an incorrect outcome — here, an incorrect difficulty target that can allow fork blocks with insufficient PoW to pass validation.

---

### Finding Description

`get_header_by_height` is implemented in `contract/src/lib.rs` to always look up from `mainchain_height_to_header`:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

When a fork block is submitted and its height falls on a retarget boundary, all three chain modules call this function to obtain the timestamp of the first block in the retarget window:

**Dogecoin** (`contract/src/dogecoin.rs`, lines 286–297) — with an explicit TODO acknowledging the problem:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

**Litecoin** (`contract/src/litecoin.rs`, lines 88–93):

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [3](#0-2) 

**Bitcoin** (`contract/src/bitcoin.rs`, lines 81–86):

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [4](#0-3) 

The correct value is the timestamp of the fork's **actual ancestor** at `height_first`, obtained by walking back through `prev_block_header`'s ancestor chain. Instead, the code silently substitutes the mainchain block's timestamp at that height — a different block entirely when a fork is being processed.

For Dogecoin specifically, after height 145,000 the `difficulty_adjustment_interval` is set to `1`:

```rust
let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
let difficulty_adjustment_interval = if new_difficulty_protocol {
    1
} else {
    config.difficulty_adjustment_interval
};
``` [5](#0-4) 

This means **every** submitted Dogecoin fork block (after height 145,000) hits the retarget path and uses the wrong timestamp. The retarget formula in `calculate_next_work_required` computes:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
``` [6](#0-5) 

If the mainchain block at `height_first` has a significantly older timestamp than the fork's actual ancestor at that height, `modulated_timespan` is inflated, the Digishield dampening still allows a meaningful shift, and the resulting target is **higher** (difficulty lower) than the protocol requires for the fork.

---

### Impact Explanation

An attacker submitting a Dogecoin fork chain (after height 145,000) can exploit the timestamp mismatch to obtain a lower required difficulty than the protocol mandates. Specifically:

1. The attacker begins a fork from a mainchain block where the mainchain block one step back (`height_first`) has a much older timestamp than the fork's own ancestor at that height.
2. The contract computes difficulty using the mainchain block's old timestamp, producing a higher target (lower difficulty).
3. The attacker's fork blocks pass the PoW check with less work than the real protocol requires.
4. If the attacker's fork accumulates more chainwork than the mainchain (easier since difficulty is understated), `reorg_chain` is triggered, corrupting the canonical chain mapping stored in `mainchain_height_to_header` and `mainchain_tip_blockhash`.
5. Downstream consumers calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` now operate against a fraudulent canonical chain, enabling false transaction inclusion proofs.

**Impact: Medium** — corrupts canonical chain state and invalidates transaction verification results for all consumers.

---

### Likelihood Explanation

For **Dogecoin** (post-height 145,000): every fork block submission triggers the retarget path, making the wrong-value substitution occur on every fork block. The attacker only needs to find a point in the mainchain where the timestamp gap between adjacent blocks is large (not uncommon in practice), then fork from there. No privileged access is required — `submit_blocks` is callable by any trusted relayer, and the `trusted_relayer` mechanism is open to staked participants.

For **Bitcoin** and **Litecoin**: the retarget boundary occurs every 2016 blocks, so the attacker must submit a fork that reaches a 2016-block boundary, which is a higher bar but not impossible for a determined attacker.

**Likelihood: Medium** (Dogecoin), **Low** (Bitcoin/Litecoin).

---

### Recommendation

Replace the `get_header_by_height` call in all three retarget functions with an ancestor-walk that starts from `prev_block_header` and steps back `blocks_to_go_back` times via `get_prev_header`. This ensures the timestamp used in the difficulty calculation always belongs to the actual ancestor of the block being validated, regardless of whether it is on the mainchain or a fork.

---

### Proof of Concept

1. Deploy the Dogecoin build of the contract (height > 145,000 in genesis).
2. Submit a legitimate mainchain sequence up to height `H`, where the mainchain block at height `H-1` has timestamp `T_main` (e.g., very old due to a slow block).
3. Submit a fork block at height `H` whose `prev_block_hash` points to the mainchain block at `H-1`, but whose fork ancestor at `H-1` (submitted earlier as a fork block) has timestamp `T_fork` >> `T_main`.
4. Observe that `get_next_work_required` computes `first_block_time = T_main` (from `get_header_by_height(H-1)`) instead of `T_fork`.
5. The inflated `modulated_timespan` produces a higher target (lower difficulty) than the fork's actual ancestor timestamps would require.
6. The attacker's fork block passes `check_pow` with less PoW than the protocol mandates.
7. If the fork's cumulative chainwork exceeds the mainchain's, `reorg_chain` executes, replacing `mainchain_tip_blockhash` with the attacker's fork tip and corrupting all height-to-hash mappings used by `verify_transaction_inclusion`.

### Citations

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

**File:** contract/src/dogecoin.rs (L291-296)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

```

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
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
