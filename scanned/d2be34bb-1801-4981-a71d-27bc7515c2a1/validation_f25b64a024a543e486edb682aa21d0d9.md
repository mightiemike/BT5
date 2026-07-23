### Title
PriceVelocityGuardExtension Bypasses Velocity Check on First Swap Due to Zero `lastMidPriceX64` Initial State — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` skips the entire velocity check when `lastMidPriceX64 == 0`. Because this storage field defaults to zero and the extension provides no `initialize` override to seed it at pool-creation time, **every pool deployed with this extension has its velocity guard silently bypassed on the first swap**, regardless of how large the oracle price movement is.

---

### Finding Description

In `beforeSwap`, the guard reads the stored prior mid-price, writes the new one, and then gates the velocity math behind a zero-check:

```solidity
uint128 prevMid = s.lastMidPriceX64;
uint64  prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // state written unconditionally
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                    // ← guard skipped when prevMid == 0
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        // velocity math and revert
    }
}
``` [1](#0-0) 

`lastMidPriceX64` is a plain storage mapping field; its default value is `0`. The extension does **not** override `initialize`, so there is no factory-enforced path to seed the price at pool-creation time. [2](#0-1) 

The only way to pre-seed the price is the admin-only `setLastMidPrice`, which is optional and not called automatically. [3](#0-2) 

This is the direct analog to the external Morpho bug: just as `getRewards` returned `0` early when `userIndex == 0` (the default for a newly listed reward token), `beforeSwap` skips the velocity check when `prevMid == 0` (the default for a newly deployed pool). In both cases the zero-initialized sentinel is treated as "no prior state → skip the guard" instead of "no prior state → fail closed."

---

### Impact Explanation

The velocity guard exists to protect LPs from rapid oracle-price movements that cause value leakage (LVR). If the oracle price moves significantly between pool deployment and the first swap, the guard fails open: the swap executes at the moved price, LPs sell tokens at a worse-than-intended rate, and the attacker (or any first swapper) captures the difference. The second swap onward is correctly gated. The loss is bounded by the magnitude of the price move and the pool's liquidity depth, but can be material for volatile assets or pools with large TVL.

---

### Likelihood Explanation

The condition is met for **every** pool that uses `PriceVelocityGuardExtension` unless the admin manually calls `setLastMidPrice` before the first swap. There is no on-chain enforcement of that precondition. Any unprivileged user can trigger the first swap; no special role or setup is required beyond the pool being live.

---

### Recommendation

**Option A (fail closed):** At the start of `beforeSwap`, if `maxChangePerBlockE18 != 0` and `prevMid == 0`, revert with a dedicated error (e.g., `PriceVelocityNotInitialized`). This forces the admin to call `setLastMidPrice` before the pool accepts swaps.

**Option B (initialize at deployment):** Override `initialize` in `PriceVelocityGuardExtension` to accept and store an initial `lastMidPriceX64` and `lastUpdateBlock`, mirroring the pattern used by `OracleValueStopLossExtension`. [4](#0-3) 

---

### Proof of Concept

1. Deploy a pool with `PriceVelocityGuardExtension`; set `maxChangePerBlockE18 = 1e15` (≈ 0.1 % per block). Do **not** call `setLastMidPrice`.
2. Oracle price moves 30 % between deployment and block N (legitimate market move).
3. At block N, any user calls `pool.swap(...)`. Inside `beforeSwap`:
   - `prevMid = priceVelocityState[pool].lastMidPriceX64` → **0**
   - `s.lastMidPriceX64` is updated to the current mid.
   - `if (prevMid != 0)` is **false** → velocity math never runs → no revert.
4. The swap settles at the 30 %-moved price; LPs absorb the full adverse selection.
5. A second swap in the same block with another 1 % move would correctly revert with `PriceVelocityExceeded`. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L19-22)
```text
contract PriceVelocityGuardExtension is BaseMetricExtension, IPriceVelocityGuardExtension {
  mapping(address pool => PriceVelocityState) public priceVelocityState;

  constructor(address factory_) BaseMetricExtension(factory_) {}
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-79)
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
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

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

    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

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
