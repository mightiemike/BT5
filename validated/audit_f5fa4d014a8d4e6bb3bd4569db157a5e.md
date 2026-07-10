The code is directly readable and the discrepancy is confirmed. Let me check the Zcash reference for the correct value.

### Title
Incorrect `MAX_FUTURE_BLOCK_TIME_MTP` Constant Causes Rejection of Valid Zcash Headers — (`btc-types/src/network.rs`, `contract/src/zcash.rs`)

### Summary

`MAX_FUTURE_BLOCK_TIME_MTP` is set to `90 * 60 = 5400` seconds, but the Zcash protocol's MTP-based future-timestamp soft fork uses `nMaxFutureBlockTime = 129600` seconds (36 hours). The contract's own comment at line 40 of `zcash.rs` explicitly acknowledges the correct value. Any valid Zcash block whose timestamp falls between MTP+5401 and MTP+129600 seconds is accepted by the real Zcash network but unconditionally rejected by this contract, causing the light client's canonical chain to diverge from the real Zcash chain.

### Finding Description

`MAX_FUTURE_BLOCK_TIME_MTP` is defined as: [1](#0-0) 

```
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;  // 5400 seconds
```

This constant is imported and enforced in `check_pow` for every submitted Zcash header: [2](#0-1) 

The code's own comment at line 40 contradicts the constant's value:

```rust
// MAX_FUTURE_BLOCK_TIME_MTP is typically 129600 seconds (36 hours) in Zcash
require!(
    block_header.time
        <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
    "time-too-far-ahead-of-mtp: ..."
);
```

The comment acknowledges 129600 seconds is the correct Zcash value, but the enforced constant is 5400 — a 24× undercount. The Zcash reference node (`src/main.cpp`, `CheckBlockHeader`) uses `nMaxFutureBlockTime = 129600`. Any block with `time ∈ (MTP+5400, MTP+129600]` is valid on-chain but will be rejected here with `"time-too-far-ahead-of-mtp"`.

### Impact Explanation

The light client's canonical chain diverges from the real Zcash chain whenever a miner produces a block with a timestamp more than 90 minutes but less than 36 hours ahead of MTP. Such blocks are fully valid under Zcash consensus rules and will be included in the real chain, but the contract will permanently reject them. A relayer cannot advance the light client past such a block, stalling proof verification for any transaction confirmed in or after that block.

### Likelihood Explanation

Zcash's 75-second block time means MTP advances slowly. During periods of low hash rate or deliberate timestamp manipulation (which miners do to influence difficulty), timestamps can legitimately exceed MTP by more than 90 minutes while remaining well within the 36-hour protocol limit. The window (5401–129600 seconds ahead of MTP) is wide enough that real-world Zcash blocks can and do fall into it.

### Recommendation

Change the constant to match the Zcash protocol value:

```rust
// btc-types/src/network.rs
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 129_600; // 36 hours, per Zcash nMaxFutureBlockTime
```

Because `MAX_FUTURE_BLOCK_TIME_MTP` is also imported by Bitcoin and Litecoin validation paths, verify whether those chains use a different value and introduce chain-specific constants if needed rather than sharing a single global.

### Proof of Concept

1. Compute `prev_mtp` for any recent Zcash block sequence.
2. Construct a header with `time = prev_mtp + 7200` (2 hours ahead of MTP — valid under Zcash rules).
3. Call the contract's header-submission entry point with this header.
4. The contract panics with `"time-too-far-ahead-of-mtp"` because `7200 > 5400`.
5. Submit the same header to a Zcash full node: it is accepted without error, confirming the divergence. [3](#0-2) [4](#0-3)

### Citations

**File:** btc-types/src/network.rs (L7-11)
```rust
/**
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * median-time-past of the previous block.
 */
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;
```

**File:** contract/src/zcash.rs (L36-45)
```rust
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
```
