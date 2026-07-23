Audit Report

## Title
`PriceVelocityGuardExtension` velocity guard silently bypassed due to uninitialized `maxChangePerBlockE18` and `lastMidPriceX64` — (File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol)

## Summary

`PriceVelocityGuardExtension` does not override `initialize()`, so `priceVelocityState[pool]` is the zero-value struct for every newly created pool. Two independent zero-guards inside `beforeSwap` each independently short-circuit the entire velocity check when either `lastMidPriceX64` or `maxChangePerBlockE18` is zero, meaning the guard provides no protection until the pool admin makes two separate post-deployment calls — and the very first swap is always unguarded regardless of partial configuration.

## Finding Description

`MetricOmmPoolFactory.createPool` calls `initialize(pool, extensionInitData[i])` on every registered extension immediately after deployment: [1](#0-0) 

`PriceVelocityGuardExtension` does not override `initialize()`, so it falls through to `BaseMetricExtension.initialize`, which is a no-op returning only the selector: [2](#0-1) 

Contrast this with `OracleValueStopLossExtension`, which properly overrides `initialize` and decodes and stores its configuration atomically at pool creation. `PriceVelocityGuardExtension` has no such override, leaving `priceVelocityState[pool]` as `{maxChangePerBlockE18: 0, lastMidPriceX64: 0, lastUpdateBlock: 0}`.

Inside `beforeSwap`, two independent zero-guards each independently bypass the entire velocity check: [3](#0-2) 

- **Guard 1** (`if (prevMid != 0)`): skips the entire check if no prior price was recorded. Since `lastMidPriceX64` starts at zero, the very first swap on every pool using this extension is always unguarded, regardless of whether `setMaxChangePerBlock` was called.
- **Guard 2** (`if (maxChange != 0)`): skips the check if the cap was never set. Since `maxChangePerBlockE18` starts at zero, all swaps are unguarded until `setMaxChangePerBlock` is called.

The two admin setters that would activate the guard are: [4](#0-3) 

Neither is called by the factory, and neither is required before the pool accepts swaps. Additionally, `PriceVelocityGuardExtension.beforeSwap` drops the `onlyPool` modifier that `BaseMetricExtension` declares on its virtual stub: [5](#0-4) [6](#0-5) 

This means any external caller can invoke `beforeSwap` and write to `priceVelocityState[msg.sender]`, though the primary impact is the initialization gap.

## Impact Explanation

A pool that registers `PriceVelocityGuardExtension` in its `BEFORE_SWAP_ORDER` provides zero velocity protection until the pool admin makes two separate post-deployment transactions. During the unconfigured window, oracle bid/ask prices — however fast they move — are accepted without any cap. If the oracle delivers a price that has moved far beyond what the pool's LP positions can absorb at fair value (e.g., due to a flash oracle update, a stale feed, or a price spike), swaps execute at that bad price. LPs receive less than the oracle mid-price implies they are owed, constituting a direct loss of LP principal. This matches the "bad-price execution" allowed impact category.

## Likelihood Explanation

The misconfiguration is the default state of every pool that uses this extension. A pool admin who deploys with `PriceVelocityGuardExtension` expecting velocity protection receives none until they discover and execute two separate admin calls. The first swap after pool creation is always unguarded regardless of whether `setMaxChangePerBlock` was called, because `lastMidPriceX64` starts at zero and `prevMid != 0` is false. The window is therefore at minimum one block wide for every pool using this extension, and potentially indefinite if the admin is unaware of the required post-deployment steps. No on-chain mechanism enforces or signals that the guard is inactive.

## Recommendation

Override `initialize()` in `PriceVelocityGuardExtension` to decode and store `maxChangePerBlockE18` and an initial `lastMidPriceX64` from the `data` parameter, mirroring the pattern used by `OracleValueStopLossExtension`. Require `maxChangePerBlockE18 > 0` during initialization so that a pool cannot be created with a silently disabled guard:

```solidity
function initialize(address pool, bytes calldata data)
    external
    override
    onlyFactory
    returns (bytes4)
{
    (uint64 maxChange, uint128 initialMid) = abi.decode(data, (uint64, uint128));
    require(maxChange > 0, "PVG: maxChange must be nonzero");
    PriceVelocityState storage s = priceVelocityState[pool];
    s.maxChangePerBlockE18 = maxChange;
    s.lastMidPriceX64 = initialMid;
    s.lastUpdateBlock = uint64(block.number);
    return IMetricOmmExtensions.initialize.selector;
}
```

## Proof of Concept

1. Pool admin calls `factory.createPool(params)` with `PriceVelocityGuardExtension` listed in `params.extensions` and wired into `BEFORE_SWAP_ORDER`. `extensionInitData[i]` is empty (or ignored, since `initialize` is not overridden).
2. Factory calls `PriceVelocityGuardExtension.initialize(pool, "")` → falls through to base no-op. `priceVelocityState[pool]` remains `{maxChangePerBlockE18: 0, lastMidPriceX64: 0, lastUpdateBlock: 0}`.
3. Admin does not call `setMaxChangePerBlock` or `setLastMidPrice` (or calls only one of them).
4. Oracle delivers a price that has moved 40% in one block.
5. Attacker calls `pool.swap(...)`.
6. Pool calls `_beforeSwap` → `PriceVelocityGuardExtension.beforeSwap` executes.
7. `prevMid = s.lastMidPriceX64 == 0` → `if (prevMid != 0)` is false → entire velocity check skipped, function returns `beforeSwap.selector`.
8. Swap executes at the 40%-moved oracle price. LPs receive far less token output than the pre-spike price would have entitled them to. Attacker profits from the price discrepancy at LP expense.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L206-210)
```text
    for (uint256 i = 0; i < params.extensions.length; i++) {
      CallExtension.callExtension(
        params.extensions[i], abi.encodeCall(IMetricOmmExtensions.initialize, (pool, params.extensionInitData[i]))
      );
    }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L41-43)
```text
  function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L24-34)
```text
  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
  }

  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-46)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L60-76)
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
      }
    }
```
