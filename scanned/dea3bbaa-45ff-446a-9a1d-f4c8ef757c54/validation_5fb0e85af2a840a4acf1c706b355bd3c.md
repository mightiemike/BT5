The full `PriceVelocityGuardExtension.beforeSwap` code is now visible. Let me trace the exact logic for the same-block scenario.

The code is fully clear. The vulnerability is real and provable from the code alone. Here is the complete analysis:

---

### Title
Per-Block Price Velocity Cap Bypassed by Multiple Same-Block Swaps via Rolling Reference Price — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`PriceVelocityGuardExtension.beforeSwap` updates `lastMidPriceX64` and `lastUpdateBlock` **before** performing the velocity check. When two or more swaps execute in the same block, each subsequent swap computes `blockDiff = 0` against the **previous swap's price** as its reference, not the block-start price. This allows each swap to move the price by the full `maxChangePerBlockE18` relative to the prior swap, compounding to a total intra-block movement of `N × maxChangePerBlockE18` without ever triggering `PriceVelocityExceeded`.

### Finding Description

The guard's stated invariant (NatSpec, line 15–17) is:

```
Allowed deviation scales as maxChangePerBlockE18 * sqrt(1 + blockDifference).
changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
```

The implementation in `beforeSwap` is:

```solidity
// Lines 54-58: state is written BEFORE the check
uint128 prevMid   = s.lastMidPriceX64;
uint64  prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64  = midPrice;          // reference advances to current swap
s.lastUpdateBlock  = uint64(block.number);

// Lines 63-74: check uses the just-overwritten prevMid/prevBlock
uint256 blockDiff = block.number - prevBlock;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
``` [1](#0-0) [2](#0-1) 

**Execution trace for two swaps in block N:**

| Event | `prevMid` | `prevBlock` | `blockDiff` | `allowedSq` | `actualSq` | Result |
|---|---|---|---|---|---|---|
| Swap 1 (block N, from block N-k) | P0 | N-k | k | `M²·(1+k)` | `(ΔP/P0)²` | passes if `≤ M` |
| Swap 2 (block N, from block N) | **P1** | **N** | **0** | **M²·1** | `(ΔP/P1)²` | passes if `≤ M` |

After swap 1: `P1 = P0 · (1 + M)`  
After swap 2: `P2 = P1 · (1 + M) = P0 · (1 + M)²`

Total intra-block movement ≈ `2M` (compounded), while the guard was intended to cap it at `M`. With N swaps: total ≈ `N·M`.

The root cause is that `lastMidPriceX64` is a **rolling per-swap reference**, not a **block-anchored reference**. The guard correctly computes `allowedSq` for the given `blockDiff`, but `blockDiff = 0` for all same-block swaps after the first, so each one is independently allowed to move by the full `maxChange`. [3](#0-2) 

### Impact Explanation

The velocity guard's sole purpose is to prevent intra-block price manipulation beyond the configured cap. This bypass allows any public user to move the oracle-derived mid-price by an unbounded multiple of `maxChangePerBlockE18` within a single block by batching swaps (e.g., via a multicall or a custom contract). Swaps execute against the manipulated mid-price, constituting bad-price execution and potential LP fund loss. The guard fails open rather than closed.

### Likelihood Explanation

The attack requires only standard public swap access — no privileged role, no malicious pool setup, no non-standard token. Any user who can call `swap` twice in the same block (trivially achievable via a contract or multicall) can exploit this. The oracle-based pool design makes this particularly impactful because the mid-price directly determines swap settlement.

### Recommendation

Track a separate **block-start price** (`blockStartMidPriceX64`) that is only updated when `block.number > lastUpdateBlock`. When `blockDiff == 0`, compare the current mid-price against `blockStartMidPriceX64` (the price at the start of the current block), not against the last swap's price. Concretely:

```solidity
if (block.number > prevBlock) {
    s.blockStartMidPriceX64 = prevMid; // anchor for this new block
}
uint128 blockRef = s.blockStartMidPriceX64;
// use blockRef (not prevMid) for delta and changeE18 computation
```

This ensures the full-block envelope is enforced regardless of how many swaps occur within the block.

### Proof of Concept

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
``` [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L54-58)
```text
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L60-74)
```text
    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
```
