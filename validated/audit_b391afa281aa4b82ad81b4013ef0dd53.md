### Title
Difficulty Boundary Block Read from Mainchain Instead of Fork Ancestor, Enabling PoW Bypass on Fork Submissions - (File: `contract/src/dogecoin.rs`)

### Summary
`get_next_work_required` in both `contract/src/dogecoin.rs` and `contract/src/bitcoin.rs` retrieves the difficulty-interval boundary block's timestamp via `get_header_by_height`, which unconditionally reads from `mainchain_height_to_header`. When a fork block is submitted that diverges before the boundary height, the mainchain block's timestamp is used instead of the fork's actual ancestor's timestamp. This produces an incorrect expected difficulty, allowing a relayer to submit fork blocks that pass the difficulty check with lower PoW than the protocol requires. The code itself flags this with a `TODO` comment at the exact site.

### Finding Description

In `contract/src/dogecoin.rs`, `get_next_work_required` computes the difficulty boundary height and then fetches the boundary block:

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

This always returns the **mainchain** block at `height_first`, not the fork's ancestor. The same pattern exists in `contract/src/bitcoin.rs`:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [3](#0-2) 

For **Dogecoin post-block 145,000** (DigiShield), `difficulty_adjustment_interval = 1`, so `height_first = prev_block_height - 1`. Any fork that diverges two or more blocks back causes the code to use the mainchain block's timestamp at `height_first` instead of the fork's actual ancestor's timestamp. The Dogecoin difficulty formula is:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
``` [4](#0-3) 

A relayer controls the timestamp of the fork block at `prev_height` (subject to MTP and `MAX_FUTURE_BLOCK_TIME_LOCAL` bounds). By setting that timestamp far ahead of the mainchain block's timestamp at `height_first`, the attacker maximises `modulated_timespan`, which is clamped to `retarget_timespan + retarget_timespan/2`. This yields a new target that is up to **1.5× the current target** — a 50% difficulty reduction — compared to what the correct ancestor timestamp would produce.

The resulting `expected_bits` is then compared against the submitted block's `bits` field:

```rust
require!(
    expected_bits == block_header.bits,
    ...
);
``` [5](#0-4) 

A block carrying the attacker-chosen lower `bits` value passes this check, and the PoW check that follows uses the same lower target:

```rust
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
    ...
);
``` [6](#0-5) 

### Impact Explanation

The corrupted invariant is `expected_bits` — the on-chain difficulty gate for fork block acceptance. Accepting a fork block with an artificially lowered `bits` value means the block's `chain_work` contribution (`work_from_bits(block_header.bits)`) is also lower per block, but the attacker compensates by submitting a longer fork. If the fork's cumulative `chain_work` exceeds `total_main_chain_chainwork`, `reorg_chain` fires:

```rust
if current_header.chain_work > total_main_chain_chainwork {
    log!("Chain reorg");
    self.reorg_chain(current_header, last_main_chain_block_height);
}
``` [7](#0-6) 

This rewrites `mainchain_tip_blockhash` and `mainchain_height_to_header`, directly corrupting the canonical chain state that `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` rely on to validate SPV proofs. A downstream contract consuming those proofs would receive incorrect inclusion results.

### Likelihood Explanation

For Dogecoin (post-145,000), the bug is triggered by any fork diverging ≥ 2 blocks back — a routine occurrence. The attacker must be a trusted relayer (the `submit_blocks` entry point is gated by `#[trusted_relayer]`), which the audit scope explicitly lists as a valid adversarial path. No additional privileges beyond relayer status are required. The attacker needs only modest hashpower to mine blocks at the reduced (up to 50% easier) difficulty.

For Bitcoin, `difficulty_adjustment_interval = 2016`, so the fork must diverge 2016+ blocks back — extremely unlikely in practice.

### Recommendation

Replace `get_header_by_height(height_first)` with an ancestor traversal that walks the fork chain backwards via `prev_block_hash` links until reaching `height_first`. This ensures the correct ancestor block is used regardless of whether the submitted block is on the mainchain or a fork. Alternatively, store the ancestor hash explicitly in `ExtendedHeader` at each difficulty boundary during block submission.

### Proof of Concept

1. Mainchain tip is at height `H`. Mainchain block at height `H-2` has timestamp `T_mc`.
2. Attacker submits a fork block at height `H-1` (diverging from the mainchain at `H-2`) with timestamp `T_fork = T_mc + 7200` (at the `MAX_FUTURE_BLOCK_TIME_LOCAL` ceiling).
3. Attacker submits a fork block at height `H`. `get_next_work_required` is called with `prev_block_header` = fork block at `H-1`.
4. `height_first = H-2`. `get_header_by_height(H-2)` returns the **mainchain** block with timestamp `T_mc`.
5. `modulated_timespan = T_fork - T_mc = 7200`, clamped to `max_timespan = retarget_timespan * 1.5 = 90s`.
6. New target = `current_target * 90 / 60` → 50% easier than correct difficulty.
7. Attacker's fork block at `H` carries `bits` encoding this easier target, passes `expected_bits == block_header.bits`, and is mined against the reduced PoW target.
8. Attacker repeats, accumulating chainwork. When fork chainwork exceeds mainchain chainwork, `reorg_chain` executes, corrupting the canonical chain state.

### Citations

**File:** contract/src/dogecoin.rs (L27-33)
```rust
        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );
```

**File:** contract/src/dogecoin.rs (L150-154)
```rust
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
```

**File:** contract/src/dogecoin.rs (L291-295)
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

**File:** contract/src/lib.rs (L563-566)
```rust
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

**File:** contract/src/bitcoin.rs (L81-86)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
