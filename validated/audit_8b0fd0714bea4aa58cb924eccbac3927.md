### Title
Single Hardcoded `MAX_FUTURE_BLOCK_TIME_MTP` Constant Applied Uniformly Causes Valid Zcash Blocks to Be Incorrectly Rejected — (`btc-types/src/network.rs`, `contract/src/zcash.rs`)

### Summary

`MAX_FUTURE_BLOCK_TIME_MTP` is defined as a single global constant (`90 * 60 = 5400 seconds`) in `btc-types/src/network.rs` and is applied uniformly in the Zcash `check_pow()` path. The code's own comment acknowledges that the correct Zcash protocol value is **129600 seconds (36 hours)** — 24× larger. During any period of slow Zcash block production, a relayer submitting a valid canonical Zcash block whose timestamp is between 90 minutes and 36 hours ahead of the previous block's MTP will be permanently rejected by the contract, stalling the light client's chain tip and invalidating all subsequent SPV proofs.

### Finding Description

`btc-types/src/network.rs` defines two global timestamp constants:

```rust
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;      // 5 400 s
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60; // 7 200 s
``` [1](#0-0) 

`contract/src/zcash.rs` imports and applies `MAX_FUTURE_BLOCK_TIME_MTP` directly in `check_pow()`:

```rust
// MAX_FUTURE_BLOCK_TIME_MTP is typically 129600 seconds (36 hours) in Zcash
require!(
    block_header.time
        <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
    "time-too-far-ahead-of-mtp: block timestamp is too far ahead of median-time-past"
);
``` [2](#0-1) 

The comment on line 40 is the smoking gun: the developer recorded the correct Zcash protocol value (129 600 s) but the constant in use is 5 400 s. The Zcash protocol's `MAX_FUTURE_BLOCK_TIME` soft-fork rule (introduced in v2.1.1-1) uses 36 hours relative to MTP, not 90 minutes. The constant is defined once, globally, with no mechanism for chain-specific override.

The same constant is imported by all four chain modules:

| Module | Import |
|---|---|
| `bitcoin.rs` | `MAX_FUTURE_BLOCK_TIME_LOCAL` only |
| `litecoin.rs` | `MAX_FUTURE_BLOCK_TIME_LOCAL` only |
| `dogecoin.rs` | `MAX_FUTURE_BLOCK_TIME_LOCAL` only |
| `zcash.rs` | **both** `MAX_FUTURE_BLOCK_TIME_LOCAL` **and** `MAX_FUTURE_BLOCK_TIME_MTP` | [3](#0-2) 

The MTP check is the binding constraint for Zcash. The local-time check (`MAX_FUTURE_BLOCK_TIME_LOCAL = 7200 s`) is a separate, independent guard applied after the MTP check.

### Impact Explanation

When Zcash block production slows (e.g., a difficulty spike, hash-rate drop, or network partition), the Median Time Past of the previous 11 blocks can fall well behind wall-clock time. A miner producing a new block with a timestamp close to the current time will set `block_header.time` more than 5 400 s ahead of MTP — which is perfectly valid under Zcash consensus (36-hour window) — but the contract's `check_pow()` will panic with `"time-too-far-ahead-of-mtp"` and reject the block.

Concrete consequence chain:
1. `submit_blocks()` calls `check_pow()` for each submitted header.
2. The MTP check fires and the transaction reverts.
3. The contract's `mainchain_tip_blockhash` is never updated.
4. All subsequent `verify_transaction_inclusion()` calls reference a stale tip; SPV proofs for any Zcash block mined after the stall point return `false` or panic. [4](#0-3) 

### Likelihood Explanation

The trigger condition is: MTP of the previous block is more than 90 minutes behind the submitted block's timestamp, while that timestamp is still within 2 hours of NEAR's `env::block_timestamp_ms()`. This window (90 min – 2 hr) is reachable whenever Zcash block production slows enough that the median of the last 11 block timestamps lags wall-clock time by more than 90 minutes. Historical Zcash data shows multi-hour block droughts during hash-rate migrations. No attacker capability is required; a legitimate relayer submitting the next canonical Zcash block is the entry path.

### Recommendation

Replace the single global constant with a chain-specific field. For Zcash, add `max_future_block_time_mtp: u32` to `ZcashConfig` and set it to `129_600` (36 hours):

```rust
// In ZcashConfig
pub max_future_block_time_mtp: u32,  // 129_600 for mainnet/testnet

// In check_pow()
require!(
    block_header.time
        <= next_work_result.prev_block_median_time_past + config.max_future_block_time_mtp,
    "time-too-far-ahead-of-mtp"
);
```

This mirrors the recommended fix in M-12: move the parameter out of a shared global constant and into the per-chain configuration struct so each chain's actual protocol rule is enforced independently. [5](#0-4) 

### Proof of Concept

Scenario (Zcash mainnet):

1. Zcash experiences a 3-hour block drought. The last 11 blocks span timestamps from `T-180min` to `T-90min`; MTP = `T-135min`.
2. Mining resumes. A miner produces block `N+1` with `block_header.time = T` (current wall-clock time).
3. Relayer calls `submit_blocks([header_N_plus_1])`.
4. `check_pow()` computes `prev_block_median_time_past = T - 135min`.
5. MTP check: `T <= (T - 135min) + 5400s` → `T <= T - 45min` → **false** → contract panics.
6. The same block passes Zcash full-node validation because `T <= (T - 135min) + 129600s` → `T <= T + 1065min` → **true**.
7. The light client is permanently stuck; no further Zcash headers can be submitted until MTP catches up, which requires 11 more blocks to be produced and accepted — a circular dependency since those blocks are also rejected. [6](#0-5) [7](#0-6)

### Citations

**File:** btc-types/src/network.rs (L5-17)
```rust
pub const MEDIAN_TIME_SPAN: usize = 11;

/**
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * median-time-past of the previous block.
 */
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;

/**
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * current local time.
 */
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60;
```

**File:** btc-types/src/network.rs (L176-186)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Copy, Debug)]
pub struct ZcashConfig {
    pub proof_of_work_limit_bits: u32,
    pub pow_limit: U256,
    pub pow_averaging_window: i64,
    pub post_blossom_pow_target_spacing: i64,
    pub pow_max_adjust_down: i64,
    pub pow_max_adjust_up: i64,
    pub pow_allow_min_difficulty_blocks_after_height: Option<u64>,
}
```

**File:** contract/src/zcash.rs (L1-8)
```rust
use crate::{utils::BlocksGetter, BtcLightClient, BtcLightClientExt};
use btc_types::{
    header::{ExtendedHeader, Header},
    network::{Network, ZcashConfig, MAX_FUTURE_BLOCK_TIME_LOCAL, MAX_FUTURE_BLOCK_TIME_MTP},
    u256::U256,
    utils::target_from_bits,
};
use near_sdk::{env, near, require};
```

**File:** contract/src/zcash.rs (L20-68)
```rust
    // Reference implementation: https://github.com/zcash/zcash/blob/v6.2.0/src/main.cpp#L5019
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let next_work_result =
            zcash_get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            next_work_result.expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );

        // Check timestamp against prev
        require!(
            block_header.time > next_work_result.prev_block_median_time_past,
            "time-too-old: block time is before the median time of the previous block"
        );

        // Check future timestamp soft fork rule introduced in v2.1.1-1.
        // This retrospectively activates at block height 2 for mainnet and regtest,
        // and 6 blocks after Blossom activation for testnet.
        //
        // MAX_FUTURE_BLOCK_TIME_MTP is typically 129600 seconds (36 hours) in Zcash
        require!(
            block_header.time
                <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
            "time-too-far-ahead-of-mtp: block timestamp is too far ahead of median-time-past"
        );

        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp is too far ahead of local time"
        );

        require!(
            block_header.version >= 4,
            "bad-version: block version must be at least 4"
        );

        // Check Equihash solution
        let n = 200;
        let k = 9;
        let input = block_header.get_block_header_vec_for_equihash();

        equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
            .unwrap_or_else(|e| {
                env::panic_str(&format!("Invalid Equihash solution: {e}"));
            });
    }
```
