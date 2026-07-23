### Title
`PriceVelocityGuardExtension.beforeSwap` Lacks `onlyPool` Guard, Allowing Any Caller to Reset the Velocity Baseline and Bypass the Price-Velocity Cap - (`File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` writes `lastMidPriceX64` and `lastUpdateBlock` to the per-pool velocity state on every call, but the function carries no `onlyPool` access control. Any external account can call it directly with crafted `bidPriceX64`/`askPriceX64` arguments to silently reset the velocity baseline to the current oracle mid price. A subsequent legitimate pool swap then compares the current price against the attacker-supplied baseline (delta ≈ 0), so the velocity cap is never triggered even when the oracle price has moved far beyond the configured envelope.

---

### Finding Description

`BaseMetricExtension` declares `beforeSwap` with an `onlyPool` modifier: [1](#0-0) 

When `PriceVelocityGuardExtension` overrides that function, it drops the modifier entirely: [2](#0-1) 

The function unconditionally writes to storage before any guard check: [3](#0-2) 

The velocity comparison only runs when `prevMid != 0` and `maxChange != 0`: [4](#0-3) 

Because the state write precedes the guard check, a direct external call with any `bidPriceX64`/`askPriceX64` pair permanently overwrites `lastMidPriceX64` and `lastUpdateBlock` for the targeted pool without the caller being a registered pool.

Contrast this with `OracleValueStopLossExtension.afterSwap`, which deliberately substitutes an `_requireInitialized` check for `onlyPool` because only the factory can call `initialize`: [5](#0-4) 

`PriceVelocityGuardExtension` has no equivalent guard — `priceVelocityState` is a plain mapping that defaults to zero and is never gated by an initialization flag.

---

### Impact Explanation

The velocity guard exists to protect LPs from oracle price manipulation: if the oracle mid price jumps more than `maxChangePerBlockE18 * sqrt(1 + blockDiff)` from the last recorded baseline, the swap reverts. An attacker who has moved the oracle price (or who simply wants to trade at a price the guard would block) can:

1. Call `PriceVelocityGuardExtension.beforeSwap` directly, passing `bidPriceX64`/`askPriceX64` that compute to the current oracle mid price.
2. This sets `lastMidPriceX64 = currentMid` and `lastUpdateBlock = block.number`.
3. In the same block, call `pool.swap(...)`. The pool invokes the extension hook; `prevMid` is now `currentMid`, so `delta = 0`, `actualSq = 0 ≤ allowedSq` — the check passes unconditionally.
4. The swap executes at the manipulated/jumped price, draining LP principal at a price the guard was configured to block.

This is a direct loss of LP funds above Sherlock thresholds: the protection the pool admin deployed is silently nullified by an unprivileged caller.

---

### Likelihood Explanation

- Pool creation is permissionless; any pool can wire `PriceVelocityGuardExtension`.
- The attack requires a single direct external call to the extension contract before the target swap — no special role, no flash loan, no multi-step setup.
- The extension address is publicly readable from the pool's immutables, so the target is trivially discoverable.
- The attack is most profitable when the oracle price has already moved significantly (e.g., during high volatility or after a price-feed update), which is exactly when the guard is most needed.

---

### Recommendation

Add `onlyPool` to the overriding `beforeSwap` in `PriceVelocityGuardExtension`, matching the base class intent:

```solidity
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
) external override onlyPool returns (bytes4) {
```

`onlyPool` is already defined in `BaseMetricExtension` and checks `IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)`: [6](#0-5) 

---

### Proof of Concept

```solidity
// Assume:
//   pool  = deployed MetricOmmPool with PriceVelocityGuardExtension wired
//   ext   = PriceVelocityGuardExtension instance
//   admin has called ext.setMaxChangePerBlock(pool, 1e16) // 1% per block cap
//   admin has called ext.setLastMidPrice(pool, P_old)    // baseline at old price
//   oracle now reports P_new >> P_old (beyond the 1% cap)

// Step 1 — attacker resets baseline (no role required)
uint128 P_new_bid = ...; // craft so mid == current oracle mid
uint128 P_new_ask = ...;
ext.beforeSwap(
    address(0), address(0), false, 0, 0, 0,
    P_new_bid, P_new_ask, ""
);
// ext.priceVelocityState[pool].lastMidPriceX64 == P_new_mid
// ext.priceVelocityState[pool].lastUpdateBlock  == block.number

// Step 2 — swap executes; hook sees delta == 0, passes
pool.swap(recipient, true, amountIn, priceLimit, "");
// LP funds drained at the manipulated price; velocity guard never fired
```

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-51)
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
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L57-58)
```text
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L199-203)
```text
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
```
