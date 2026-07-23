### Title
`PriceVelocityGuardExtension` silently fails open when `maxChangePerBlockE18` is never initialized — (File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol)

---

### Summary

`PriceVelocityGuardExtension` has no `initialize()` override, so `maxChangePerBlockE18` defaults to zero for every pool that registers it. The `beforeSwap` guard contains an explicit `if (maxChange != 0)` short-circuit that skips all velocity enforcement when the value is zero. Any pool deployed with this extension in its extension orders has **no velocity protection** until the admin separately calls `setMaxChangePerBlock()`, and there is no on-chain mechanism to enforce or detect that this call was ever made.

---

### Finding Description

`OracleValueStopLossExtension` overrides `initialize()` and decodes its critical parameters (`drawdownE6`, `decayPerSecondE8`, `timelock`) from factory-supplied `extensionInitData` at pool creation time, making misconfiguration impossible without an explicit zero value.

`PriceVelocityGuardExtension` does **not** override `initialize()`. It inherits the no-op base implementation:

```solidity
// BaseMetricExtension.sol – inherited by PriceVelocityGuardExtension unchanged
function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
}
``` [1](#0-0) 

The only way to arm the guard is a post-deployment admin call:

```solidity
function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18)
    external onlyPoolAdmin(pool_) { ... }
``` [2](#0-1) 

Until that call is made, `priceVelocityState[pool_].maxChangePerBlockE18 == 0`. The guard's enforcement block is:

```solidity
if (prevMid != 0) {
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {          // ← always false; entire check is skipped
        ...
        if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
    }
}
``` [3](#0-2) 

The guard still updates `lastMidPriceX64` and `lastUpdateBlock` on every swap, giving the appearance of activity, but the revert path is permanently unreachable while `maxChangePerBlockE18 == 0`.

---

### Impact Explanation

The velocity guard exists to prevent rapid oracle-price manipulation from draining LP bins. With the guard silently disabled, a large oracle price jump in a single block reaches the pool's swap math unconstrained. Arbitrageurs can extract value from LP bins at the manipulated price before the oracle corrects, causing direct loss of LP principal. This matches the allowed impact gate: *bad-price execution — unbounded bid/ask quote reaches a pool swap* and *broken core pool functionality causing loss of LP funds*.

---

### Likelihood Explanation

Every pool that lists `PriceVelocityGuardExtension` in its extension orders is born with the guard disabled. The factory calls `initialize()` on the extension at deployment, but the no-op base implementation stores nothing. There is no on-chain check, event, or revert that signals the guard is unarmed. A pool operator who deploys the extension expecting protection and then delays or forgets the `setMaxChangePerBlock()` call leaves LPs fully exposed for that entire window — or permanently.

---

### Recommendation

Add an `initialize()` override to `PriceVelocityGuardExtension` that decodes and stores `maxChangePerBlockE18` (and optionally `lastMidPriceX64`) from the factory-supplied `extensionInitData`, mirroring the pattern in `OracleValueStopLossExtension`:

```solidity
function initialize(address pool, bytes calldata data)
    external override onlyFactory returns (bytes4)
{
    (uint64 maxChange, uint128 initialMid) =
        abi.decode(data, (uint64, uint128));
    require(maxChange != 0, "velocity cap must be nonzero");
    PriceVelocityState storage s = priceVelocityState[pool];
    s.maxChangePerBlockE18 = maxChange;
    if (initialMid != 0) {
        s.lastMidPriceX64  = initialMid;
        s.lastUpdateBlock  = uint64(block.number);
    }
    emit MaxChangePerBlockSet(pool, maxChange);
    return IMetricOmmExtensions.initialize.selector;
}
``` [4](#0-3) 

This ensures the guard is armed atomically at pool creation and that a zero cap is rejected at the factory level, closing the silent-fail-open window.

---

### Proof of Concept

1. Deploy a pool via `MetricOmmPoolFactory.createPool()` with `PriceVelocityGuardExtension` listed in `extensionOrders.beforeSwap`. Pass any (or empty) `extensionInitData` — the no-op `initialize()` ignores it.
2. Do **not** call `setMaxChangePerBlock()`.
3. Observe `priceVelocityState[pool].maxChangePerBlockE18 == 0`.
4. Trigger a swap. The oracle price may have moved 50 % in one block.
5. `beforeSwap` executes: `prevMid` is set from the prior swap (or 0 on first), `maxChange == 0`, the inner `if` is skipped, no revert.
6. The swap settles at the manipulated price; LP bins absorb the loss.
7. Repeat across blocks — the guard never fires regardless of price movement magnitude. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L41-43)
```text
  function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L24-27)
```text
  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
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
