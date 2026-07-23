The code at lines 54–58 and 60–74 exactly matches the claim. The state update (`s.lastMidPriceX64 = midPrice`, `s.lastUpdateBlock = uint64(block.number)`) happens **before** the velocity check, and `blockDiff = block.number - prevBlock` evaluates to `0` for every same-block swap after the first, making `allowedSq = maxChange² * 1` for each subsequent swap. The vulnerability is confirmed by the production code.

Audit Report

## Title
Per-Block Price Velocity Cap Bypassed by Multiple Same-Block Swaps via Rolling Reference Price — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary

`PriceVelocityGuardExtension.beforeSwap` writes `lastMidPriceX64` and `lastUpdateBlock` to storage before performing the velocity check. For every swap after the first within the same block, `blockDiff` evaluates to `0`, so `allowedSq = maxChange²`, and each swap is independently permitted to move the price by the full `maxChangePerBlockE18`. This allows an attacker to compound N full-cap movements in a single block, bypassing the guard's stated per-block invariant.

## Finding Description

In `beforeSwap`, storage is updated at lines 57–58 before the check at lines 60–74:

```solidity
// Lines 54-55: read prior state
uint128 prevMid   = s.lastMidPriceX64;
uint64  prevBlock = s.lastUpdateBlock;

// Lines 57-58: overwrite state BEFORE check
s.lastMidPriceX64  = midPrice;
s.lastUpdateBlock  = uint64(block.number);

// Lines 63-70: check uses prevMid/prevBlock (rolling, not block-anchored)
uint256 blockDiff = block.number - prevBlock;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
``` [1](#0-0) [2](#0-1) 

For two swaps in block N (last update was block N-k):

| Event | `prevBlock` | `blockDiff` | `allowedSq` | Result |
|---|---|---|---|---|
| Swap 1 | N-k | k | M²·(1+k) | passes if ≤ M |
| Swap 2 | **N** | **0** | **M²·1** | passes if ≤ M |

After swap 1: `P1 = P0·(1+M)`. After swap 2: `P2 = P1·(1+M) = P0·(1+M)²`. Total intra-block movement ≈ 2M, while the guard was intended to cap it at M. With N swaps: total ≈ N·M. The root cause is that `lastMidPriceX64` is a rolling per-swap reference rather than a block-anchored reference. [3](#0-2) 

## Impact Explanation

The velocity guard's sole purpose is to prevent intra-block oracle price manipulation beyond the configured cap. This bypass allows the oracle-derived mid-price (which directly determines swap settlement) to be moved by an unbounded multiple of `maxChangePerBlockE18` within a single block. This constitutes bad-price execution and potential LP fund loss, matching the allowed impact gate for bad-price execution and direct loss of LP assets.

## Likelihood Explanation

The attack requires only standard public swap access — no privileged role, no malicious pool setup, no non-standard token. Any user who can call `swap` twice in the same block (trivially achievable via a contract or multicall) can exploit this. The oracle-based pool design makes this particularly impactful because the mid-price directly determines swap settlement.

## Recommendation

Track a separate block-start price (`blockStartMidPriceX64`) that is only updated when `block.number > lastUpdateBlock`. When `blockDiff == 0`, compare the current mid-price against `blockStartMidPriceX64` (the price at the start of the current block), not against the last swap's price:

```solidity
if (block.number > prevBlock) {
    s.blockStartMidPriceX64 = prevMid; // anchor for this new block
}
uint128 blockRef = s.blockStartMidPriceX64;
// use blockRef (not prevMid) for delta and changeE18 computation
```

This ensures the full-block envelope is enforced regardless of how many swaps occur within the block.

## Proof of Concept

```solidity
// Foundry test sketch
function test_sameBlockVelocityBypass() public {
    // Setup: pool with PriceVelocityGuard, maxChangePerBlockE18 = 0.05e18 (5%)
    // Initial mid-price P0 set at block 100

    vm.roll(101); // advance to block 101

    // Swap 1: move price from P0 to P0 * 1.05 (exactly at cap)
    // blockDiff = 101 - 100 = 1, allowedSq = M²*2, passes
    pool.swap(...); // price now P1 = P0 * 1.05

    // Swap 2: same block 101, move price from P1 to P1 * 1.05
    // blockDiff = 101 - 101 = 0, allowedSq = M²*1, passes
    pool.swap(...); // price now P2 = P0 * 1.1025

    // Total movement: 10.25% in one block, cap was 5%
    // No PriceVelocityExceeded was ever thrown
    assertGt(P2, P0 * 110 / 100); // >10% movement, guard never triggered
}
```

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L54-58)
```text
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L63-70)
```text
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
```
