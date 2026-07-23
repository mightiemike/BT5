Audit Report

## Title
`PriceVelocityGuardExtension.beforeSwap()` velocity guard unconditionally skipped on first swap due to zero-initialized `lastMidPriceX64` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary
`PriceVelocityGuardExtension.beforeSwap()` wraps its entire velocity enforcement block inside `if (prevMid != 0)`. Because `lastMidPriceX64` is zero-initialized by Solidity and `PriceVelocityGuardExtension` never overrides `initialize()` to seed it, the guard is silently skipped on every pool's first swap. Any unprivileged caller executing that first swap bypasses the configured velocity cap entirely, regardless of how far the oracle mid-price has moved.

## Finding Description
In `beforeSwap()`, the stored reference price is read into `prevMid` before the guard runs:

```solidity
// PriceVelocityGuardExtension.sol L53-76
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;   // 0 on first swap (Solidity default)
uint64  prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // state written before the guard
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                    // ← always false on first swap
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        // squared-change velocity check — never reached
        if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
    }
}
```

`PriceVelocityGuardExtension` does not override `initialize()`, so the inherited no-op from `BaseMetricExtension` is called at pool creation:

```solidity
// BaseMetricExtension.sol L41-43
function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
}
```

`lastMidPriceX64` is never seeded during initialization. The only setter is `setLastMidPrice` (L29-34), which is `onlyPoolAdmin`. If the admin does not call it before the pool opens, `prevMid` is 0 on the first swap and the guard is unconditionally skipped. After that first swap, `lastMidPriceX64` is populated and the guard activates for all subsequent swaps.

## Impact Explanation
The velocity guard is the primary defense against oracle-manipulation attacks that would let a trader extract value from LPs at a manipulated price. On the first swap the guard is entirely absent: a trader can execute at any oracle price, no matter how large the single-block move. This constitutes bad-price execution — an unclamped oracle-derived bid/ask quote reaching a pool swap — and directly enables loss of LP principal through oracle manipulation on every newly deployed pool using this extension.

## Likelihood Explanation
Every pool that deploys `PriceVelocityGuardExtension` in its `beforeSwap` order and has `maxChangePerBlockE18 > 0` is vulnerable from deployment until the first swap lands. No special role or permission is required; the first public swap triggers the bypass automatically. The admin can close the window by calling `setLastMidPrice` before the pool opens, but there is no on-chain enforcement of that ordering, making the vulnerable window guaranteed to exist on every newly deployed pool unless the admin acts proactively.

## Recommendation
Override `initialize()` in `PriceVelocityGuardExtension` to seed `lastMidPriceX64` at pool creation:

```solidity
function initialize(address pool, bytes calldata data) external override onlyFactory returns (bytes4) {
    uint128 initialMidPriceX64 = abi.decode(data, (uint128));
    require(initialMidPriceX64 != 0, "PriceVelocityGuard: zero initial price");
    PriceVelocityState storage s = priceVelocityState[pool];
    s.lastMidPriceX64 = initialMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    return IMetricOmmExtensions.initialize.selector;
}
```

Alternatively, treat `prevMid == 0` as an uninitialized sentinel and revert rather than skip:

```solidity
if (prevMid == 0) revert PriceVelocityNotInitialized();
```

## Proof of Concept
1. Deploy a pool with `PriceVelocityGuardExtension` in `beforeSwap` order and `maxChangePerBlockE18 = 1e15` (0.1% per block).
2. Do **not** call `setLastMidPrice`.
3. Wait one block; oracle price moves 50% (e.g., via oracle manipulation).
4. Call `pool.swap(...)` as any unprivileged user.
5. Inside `beforeSwap`: `prevMid = priceVelocityState[pool].lastMidPriceX64 = 0` (L54).
6. `if (prevMid != 0)` (L60) is false → velocity check is skipped entirely.
7. Swap executes at the 50%-moved oracle price; `lastMidPriceX64` is now set to the new mid (L57).
8. All subsequent swaps are correctly guarded — the bypass is consumed in one shot per pool lifetime. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-34)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-76)
```text
    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

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
      }
    }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L41-43)
```text
  function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
  }
```
