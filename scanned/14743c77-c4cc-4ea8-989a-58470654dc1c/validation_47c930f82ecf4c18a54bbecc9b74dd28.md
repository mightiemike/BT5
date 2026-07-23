### Title
First-Swap Velocity Guard Bypass Due to Uninitialized `lastMidPriceX64` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally skips the entire velocity check when `prevMid == 0`. Because `lastMidPriceX64` defaults to zero and is only written by `setLastMidPrice` (a manual admin call) or by `beforeSwap` itself, the guard is silently bypassed on every pool's first swap. Any oracle price movement — however extreme — passes unchecked on that first call.

---

### Finding Description

In `beforeSwap`, the guard reads the stored state into locals, writes the new state, and then gates the check on `prevMid != 0`:

```solidity
uint128 prevMid = s.lastMidPriceX64;   // 0 on first swap
uint64  prevBlock = s.lastUpdateBlock;  // 0 on first swap

s.lastMidPriceX64 = midPrice;          // written for next call
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                    // FALSE on first swap → entire check skipped
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;
        ...
        if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
    }
}
``` [1](#0-0) 

The only way to pre-seed `lastMidPriceX64` is through `setLastMidPrice`, which is an optional, manual pool-admin call with no enforcement at pool creation or extension initialization:

```solidity
function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    ...
}
``` [2](#0-1) 

Unlike `OracleValueStopLossExtension`, `PriceVelocityGuardExtension` has no `initialize` hook called by the factory at pool creation, so there is no guaranteed initialization path. [3](#0-2) 

The `PriceVelocityState` struct stores all three fields packed together; none are initialized at construction:

```solidity
struct PriceVelocityState {
    uint128 lastMidPriceX64;   // defaults to 0
    uint64  lastUpdateBlock;   // defaults to 0
    uint64  maxChangePerBlockE18;
}
``` [4](#0-3) 

---

### Impact Explanation

The velocity guard is the sole on-chain mechanism preventing a swap from executing when the oracle mid-price has jumped by more than `maxChangePerBlockE18 * sqrt(1 + blockDiff)` since the last observed price. Bypassing it on the first swap means:

- A pool configured with a tight velocity cap (e.g., 1 % per block) provides **zero protection** on its first live swap.
- If the oracle price has moved dramatically at the moment of the first swap — whether through legitimate market volatility or oracle manipulation — the swap executes at the extreme price, draining LP token balances at an off-market rate.
- LP principal loss is direct: the pool settles token transfers at the unchecked oracle price, and the attacker (or any first swapper) receives tokens at a rate the guard was explicitly configured to block.

The pool's `swap` function calls `_beforeSwap` before executing any trade math, so the guard is the only pre-trade price-velocity check: [5](#0-4) 

---

### Likelihood Explanation

- Every pool that attaches `PriceVelocityGuardExtension` without a prior `setLastMidPrice` call is vulnerable on its first swap — this is the default deployment path since no factory-level initialization enforces it.
- A watcher can trivially detect a new pool deployment on-chain and race to be the first swapper.
- No special privilege is required: any address that can call `pool.swap(...)` can trigger the bypass.
- The window is exactly one swap; after the first swap, `lastMidPriceX64` is non-zero and the guard activates normally.

---

### Recommendation

Add an `initialize` hook to `PriceVelocityGuardExtension` (mirroring `OracleValueStopLossExtension`) that seeds `lastMidPriceX64` and `lastUpdateBlock` at pool-creation time using the pool's live oracle price. Alternatively, treat `prevMid == 0` as a sentinel that **fails closed** (reverts) rather than silently passing, forcing the pool admin to call `setLastMidPrice` before the first swap is permitted.

```solidity
// Option A: fail closed until seeded
if (prevMid == 0) revert PriceVelocityNotInitialized();

// Option B: initialize hook (preferred)
function initialize(address pool, bytes calldata data)
    external onlyFactory returns (bytes4)
{
    (uint128 seedMid) = abi.decode(data, (uint128));
    priceVelocityState[pool].lastMidPriceX64 = seedMid;
    priceVelocityState[pool].lastUpdateBlock  = uint64(block.number);
    return IMetricOmmExtensions.initialize.selector;
}
```

---

### Proof of Concept

1. Deploy a pool with `PriceVelocityGuardExtension` attached as a `beforeSwap` hook and `maxChangePerBlockE18` set to `1e16` (1 % per block). Do **not** call `setLastMidPrice`.
2. Observe that `priceVelocityState[pool].lastMidPriceX64 == 0`.
3. Arrange for the oracle to report a price 50× higher than the pool's initial liquidity was deposited at (simulating a manipulated or flash-crashed oracle).
4. Call `pool.swap(...)`. The `beforeSwap` hook fires, reads `prevMid = 0`, skips the `if (prevMid != 0)` block entirely, and returns the valid selector.
5. The swap executes at the 50× price, draining LP token0 or token1 at a rate the guard was configured to block.
6. Call `pool.swap(...)` a second time with the same extreme price. Now `prevMid != 0` and `actualSq >> allowedSq`, so `PriceVelocityExceeded` reverts — confirming the guard works correctly from the second swap onward, and that only the first swap was unprotected. [6](#0-5)

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

**File:** metric-periphery/contracts/interfaces/extensions/IPriceVelocityGuardExtension.sol (L7-11)
```text
  struct PriceVelocityState {
    uint128 lastMidPriceX64;
    uint64 lastUpdateBlock;
    uint64 maxChangePerBlockE18;
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
