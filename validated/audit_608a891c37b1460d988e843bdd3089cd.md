The full `PriceVelocityGuardExtension.sol` is available. Let me trace the exact logic carefully.

The vulnerability is real and provable from the code. Here is the finding:

---

### Title
Per-Block Price Velocity Cap Bypassed by Same-Block Multi-Swap Reference Reset — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally writes the current mid-price and `block.number` into storage **before** performing the velocity check. As a result, every subsequent swap in the same block is checked against the **previous swap's price** (not the block-start price) with `blockDiff = 0`. An attacker can chain N public swaps in one block, each moving price by exactly `maxChangePerBlockE18`, for a total per-block move of `N × maxChange`, completely defeating the guard's stated invariant.

### Finding Description

In `beforeSwap`, the state update and the guard check are ordered as follows:

```solidity
uint128 prevMid   = s.lastMidPriceX64;   // L54 – snapshot old price
uint64  prevBlock = s.lastUpdateBlock;    // L55 – snapshot old block

s.lastMidPriceX64  = midPrice;           // L57 – WRITE new price (before check)
s.lastUpdateBlock  = uint64(block.number); // L58 – WRITE block.number (before check)

if (prevMid != 0) {
    uint256 blockDiff = block.number - prevBlock;          // L63
    uint256 allowedSq = maxChange² × (1 + blockDiff);     // L70
    if (actualSq > allowedSq) revert PriceVelocityExceeded(...); // L72-73
}
``` [1](#0-0) [2](#0-1) 

After the first swap in block B executes, storage holds `lastMidPriceX64 = P1` and `lastUpdateBlock = B`. Every subsequent swap in the same block sees `prevBlock = B`, so `blockDiff = block.number − B = 0`, giving `allowedSq = maxChange² × 1`. Each swap is therefore permitted to move price by a full `maxChange` from the **previous swap's price**, not from the block-start price. The guard's NatSpec states it "Caps how fast the provided price can move **between blocks**", but the implementation caps only per-swap movement. [3](#0-2) 

### Impact Explanation

An attacker executing N swaps in one block can move the pool mid-price by `N × maxChangePerBlockE18` total, while the guard emits no `PriceVelocityExceeded` revert. LP bins priced along the manipulated curve are drained at prices far outside the intended velocity envelope. This is a direct loss of LP principal through bad-price execution — the exact impact the guard was deployed to prevent.

### Likelihood Explanation

The attack requires only public `pool.swap()` calls in a single block. No privileged role, oracle admin, or special token behavior is needed. On L2 chains (where Metric OMM is deployed) an attacker controls transaction ordering within their own bundle, making same-block multi-swap sequences trivially achievable.

### Recommendation

Track the **block-start price** separately from the per-swap reference. Only update `lastMidPriceX64` when `block.number > lastUpdateBlock`. When `blockDiff == 0`, keep `prevMid` as the block-start price and check the cumulative move from that anchor:

```solidity
if (block.number > prevBlock) {
    s.lastMidPriceX64  = midPrice;
    s.lastUpdateBlock  = uint64(block.number);
} else {
    // same block: do NOT advance the reference price;
    // prevMid already holds the block-start anchor
}
// then perform the velocity check against prevMid / prevBlock as before
```

This ensures that no matter how many swaps occur in one block, the total allowed price movement is bounded by `maxChangePerBlockE18 × sqrt(1 + blockDiff)` measured from the block-start price.

### Proof of Concept

```
Block B-1: admin calls setLastMidPrice → lastMidPriceX64 = P0, lastUpdateBlock = B-1

Block B, tx 1 (attacker):
  pool.swap(...)  →  beforeSwap called
    prevMid = P0, prevBlock = B-1
    s.lastMidPriceX64 = P1  (P1 = P0 * (1 + maxChange))
    s.lastUpdateBlock = B
    blockDiff = 1, allowedSq = maxChange² * 2  → passes (actual = maxChange²)

Block B, tx 2 (attacker, same block):
  pool.swap(...)  →  beforeSwap called
    prevMid = P1, prevBlock = B          ← reference is now P1, not P0
    s.lastMidPriceX64 = P2  (P2 = P1 * (1 + maxChange))
    s.lastUpdateBlock = B
    blockDiff = 0, allowedSq = maxChange² * 1  → passes (actual = maxChange²)

Result: price moved from P0 to P2 ≈ P0 * (1 + maxChange)²
        ≈ P0 * (1 + 2*maxChange) for small maxChange
        Total move ≈ 2*maxChange, guard never reverted.
```

Repeat for N transactions: total move ≈ N × maxChange, all within the same block, with zero reverts.

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L9-18)
```text
/// @title PriceVelocityGuardExtension
/// @notice Caps how fast the provided price can move between blocks, per pool.
/// @dev This extension allows the pool admin to increase security of the pool by limiting price
///      manipulation through velocity constraints. However, it assumes that the pool admin is not
///      an adversary and acts to optimize pool profitability. The pool admin must be trusted.
///
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
///      where 1e18 = 100% (full unit).
```

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
