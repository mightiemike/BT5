All cited code is confirmed against the actual repository. The analysis is accurate:

- `prevMid = s.lastMidPriceX64` is read from a zero-initialized mapping slot, then the new price is written to storage **before** the check at line 60. [1](#0-0) 
- `PriceVelocityGuardExtension` has no `initialize` override; the base implementation is a no-op returning only the selector. [2](#0-1) 
- `setLastMidPrice` is the only seeding path and is `onlyPoolAdmin`. [3](#0-2) 
- `_beforeSwap` is called unconditionally before swap execution. [4](#0-3) 

---

Audit Report

## Title
`PriceVelocityGuardExtension.beforeSwap` Skips Velocity Check When `prevMid == 0`, Allowing First-Swap Guard Bypass — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary
`PriceVelocityGuardExtension` stores per-pool state in a zero-initialized mapping. On the first swap of any pool using this extension, `prevMid` is always `0`, causing the entire velocity-check block to be skipped. Any public user who executes the first swap bypasses the configured `maxChangePerBlockE18` cap, regardless of how large the oracle price jump is, because no deployment-time initialization path seeds `lastMidPriceX64`.

## Finding Description
`priceVelocityState[pool].lastMidPriceX64` and `lastUpdateBlock` are both zero at deployment due to Solidity's default storage initialization. Inside `beforeSwap`, the hook reads `prevMid = s.lastMidPriceX64`, then immediately overwrites storage with the current mid-price before performing any check:

```solidity
s.lastMidPriceX64 = midPrice;
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {   // ← entire check skipped when prevMid == 0
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        ...
        if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
    }
}
```

`PriceVelocityGuardExtension` does not override `initialize`; the base `BaseMetricExtension.initialize` is a no-op that returns only the selector. The only path to seed `lastMidPriceX64` is `setLastMidPrice`, which is restricted to `onlyPoolAdmin`. There is no enforcement that this must be called before the first swap. `MetricOmmPool.swap` calls `_beforeSwap` unconditionally before executing any trade, so the extension hook is always reached, but the velocity invariant is not enforced on the first call.

## Impact Explanation
On the first swap of any pool using `PriceVelocityGuardExtension`, the configured `maxChangePerBlockE18` cap is never applied. If the oracle price has moved significantly between pool deployment and the first swap (e.g., due to a large market move, feed update lag, or a manipulated Pyth/Chainlink price), the pool executes the trade at the unchecked price. LPs bear the full adverse-selection loss that the velocity guard was deployed to prevent. This constitutes **bad-price execution**: an unclamped oracle quote reaches a live pool swap, matching the allowed impact gate.

## Likelihood Explanation
The trigger is fully unprivileged: any public caller can be the first swapper. The pool admin must separately call `setLastMidPrice` before the first swap to arm the guard; there is no deployment-time initialization path. Pools that go live without this manual step — a realistic operational gap — are permanently exposed on their first trade. The likelihood is **medium**: the window is bounded to the first swap, but that window is open by default on every newly deployed pool using this extension.

## Recommendation
Override `initialize` in `PriceVelocityGuardExtension` to derive and store the initial mid-price from the pool's price provider at initialization time. Alternatively, treat `prevMid == 0` as a hard revert when `maxChangePerBlockE18 != 0`, forcing the pool admin to explicitly seed the reference before any swap is permitted:

```solidity
if (prevMid == 0) {
    if (s.maxChangePerBlockE18 != 0) revert PriceVelocityNotInitialized();
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

## Proof of Concept
1. Deploy `PriceVelocityGuardExtension` and a pool that registers it in `BEFORE_SWAP_ORDER`.
2. Call `setMaxChangePerBlock(pool, 1e15)` (0.1% per block cap) — guard is now configured.
3. Do **not** call `setLastMidPrice`. `priceVelocityState[pool].lastMidPriceX64 == 0`.
4. Oracle price moves 50% (e.g., feed update between deployment and first swap).
5. Any public user calls `pool.swap(...)`. `_beforeSwap` dispatches to `PriceVelocityGuardExtension.beforeSwap`.
6. Inside the hook: `prevMid = 0`, so the `if (prevMid != 0)` block is skipped entirely. No revert.
7. The swap executes at the 50%-moved oracle price. `lastMidPriceX64` is now set to the new price.
8. A second swap in the same block attempting a further 1% move would correctly revert with `PriceVelocityExceeded` — but the first, larger move was never checked.

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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-60)
```text
    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L41-43)
```text
  function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```
