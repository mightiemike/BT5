### Title
`PriceVelocityGuardExtension` never overrides `initialize()` to seed `lastMidPriceX64`, so the velocity guard is unconditionally bypassed on every pool's first swap — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` inherits `BaseMetricExtension.initialize()` without overriding it. The base implementation is a no-op that sets no per-pool state. Consequently `priceVelocityState[pool].lastMidPriceX64` is zero at pool creation and remains zero until the first live swap writes it. The guard's enforcement branch is gated on `prevMid != 0`, so the entire velocity check is skipped on the first swap regardless of how large the oracle price move is. Any pool that relies on this extension for oracle-manipulation protection is fully unprotected on its opening trade.

---

### Finding Description

`OracleValueStopLossExtension` correctly overrides `initialize()` and decodes `extensionInitData` to populate its per-pool config at factory time: [1](#0-0) 

`PriceVelocityGuardExtension` does **not** override `initialize()`. It falls through to the base no-op: [2](#0-1) 

The factory calls `initialize` on every extension immediately after deployment, passing `extensionInitData[i]`: [3](#0-2) 

Because `PriceVelocityGuardExtension.initialize()` is a no-op, `priceVelocityState[pool].lastMidPriceX64` stays at its default value of `0`.

In `beforeSwap`, the guard reads `prevMid` and immediately writes the new mid price, then checks: [4](#0-3) 

On the first swap `prevMid == 0`, so the `if (prevMid != 0)` branch is never entered. The `PriceVelocityExceeded` revert cannot fire. After that first swap `lastMidPriceX64` is set to the current oracle mid, and all subsequent swaps are checked normally.

The admin-callable `setLastMidPrice()` exists but is not called by the factory and is not enforced before the first swap: [5](#0-4) 

---

### Impact Explanation

The velocity guard's sole purpose is to block swaps when the oracle mid price has moved faster than `maxChangePerBlockE18 * sqrt(1 + blockDiff)` — the documented oracle-manipulation defence. On the first swap that protection is absent. A trader who can influence the oracle price (or who simply observes a large oracle move and races to be first) can execute a swap at an arbitrarily large price deviation without triggering `PriceVelocityExceeded`. The pool executes the swap at the manipulated oracle bid/ask, giving the attacker more output tokens than the legitimate price permits — a direct loss of LP principal.

---

### Likelihood Explanation

Every pool that deploys `PriceVelocityGuardExtension` is affected on its very first swap. The window is narrow (one transaction) but deterministic and requires no special privilege: any public caller can be the first swapper. The admin can close the window by calling `setLastMidPrice()` before the first swap, but the protocol does not enforce this and the `initialize()` path provides no mechanism to do so atomically at deployment.

---

### Recommendation

Override `initialize()` in `PriceVelocityGuardExtension` to decode `extensionInitData` and seed both `lastMidPriceX64` and `maxChangePerBlockE18` atomically at pool creation, mirroring the pattern used by `OracleValueStopLossExtension`:

```diff
+  function initialize(address pool, bytes calldata data)
+    external
+    override
+    onlyFactory
+    returns (bytes4)
+  {
+    (uint128 initialMidPriceX64, uint64 maxChangePerBlockE18_) = abi.decode(data, (uint128, uint64));
+    PriceVelocityState storage s = priceVelocityState[pool];
+    s.lastMidPriceX64   = initialMidPriceX64;
+    s.lastUpdateBlock   = uint64(block.number);
+    s.maxChangePerBlockE18 = maxChangePerBlockE18_;
+    emit LastMidPriceUpdated(pool, initialMidPriceX64);
+    emit MaxChangePerBlockSet(pool, maxChangePerBlockE18_);
+    return IMetricOmmExtensions.initialize.selector;
+  }
```

This ensures the guard is active from the very first swap, exactly as the pool creator intends when they configure the extension.

---

### Proof of Concept

1. Deploy a pool with `PriceVelocityGuardExtension` as a `beforeSwap` hook.
2. Pool admin calls `setMaxChangePerBlock(pool, 1e15)` (0.1 % per block cap).
3. Admin does **not** call `setLastMidPrice()` (the factory never does it either).
4. Oracle price jumps 50 % in one block (simulated manipulation).
5. Attacker calls `pool.swap(...)` — first swap on this pool.
6. Inside `beforeSwap`: `prevMid = priceVelocityState[pool].lastMidPriceX64 = 0` → `if (prevMid != 0)` is `false` → no velocity check → swap executes at the 50 %-deviated oracle price.
7. `lastMidPriceX64` is now set to the manipulated price.
8. Attacker profits from the mispriced swap; LPs bear the loss.
9. All subsequent swaps are correctly velocity-checked — only the first swap is unprotected.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L46-68)
```text
  function initialize(address pool, bytes calldata data)
    external
    override(BaseMetricExtension, IOracleValueStopLossExtension)
    onlyFactory
    returns (bytes4)
  {
    if (oracleStopLossConfig[pool].initialized) {
      revert OracleStopLossAlreadyInitialized(pool);
    }

    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });

    emit OracleStopLossDrawdownSet(pool, drawdownE6);
    emit OracleStopLossDecaySet(pool, decayPerSecondE8);
    emit OracleStopLossTimelockSet(pool, timelock);
    return IMetricOmmExtensions.initialize.selector;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L41-43)
```text
  function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L206-210)
```text
    for (uint256 i = 0; i < params.extensions.length; i++) {
      CallExtension.callExtension(
        params.extensions[i], abi.encodeCall(IMetricOmmExtensions.initialize, (pool, params.extensionInitData[i]))
      );
    }
```

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
