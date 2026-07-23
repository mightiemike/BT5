### Title
Unprivileged Caller Can Manipulate `PriceVelocityGuardExtension` State, Bypassing the Velocity Guard for Any Pool — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` overrides the base class hook but **drops the `onlyPool` modifier**, allowing any unprivileged address to call it directly with arbitrary `bidPriceX64`/`askPriceX64` values. This lets an attacker overwrite `lastMidPriceX64` and `lastUpdateBlock` for any pool to any value, completely neutralizing the velocity guard before executing a swap at a price the guard was configured to block.

---

### Finding Description

`BaseMetricExtension` declares `beforeSwap` with an `onlyPool` modifier that gates entry to addresses registered in the factory: [1](#0-0) 

`PriceVelocityGuardExtension` overrides this hook but **omits `onlyPool`**: [2](#0-1) 

Because the override is not `view`, it writes to persistent storage unconditionally: [3](#0-2) 

The velocity check only fires when `prevMid != 0` and `maxChange != 0`: [4](#0-3) 

An attacker calls the extension directly (not through the pool), supplying `bidPriceX64`/`askPriceX64` that produce a `midPrice` equal to the current oracle mid. This sets `lastMidPriceX64 = currentOracleMid` and `lastUpdateBlock = block.number`. When the real pool subsequently calls `beforeSwap` in the same block, `delta = 0`, `actualSq = 0 ≤ allowedSq`, and the guard passes unconditionally.

By contrast, `OracleValueStopLossExtension.afterSwap` — which also drops `onlyPool` — compensates with `_requireInitialized(msg.sender)`, which only passes for factory-registered pools: [5](#0-4) 

`PriceVelocityGuardExtension` has no equivalent guard.

---

### Impact Explanation

The velocity guard is the pool admin's last line of defense against swaps executing at oracle prices that have moved too fast (e.g., during oracle manipulation or extreme volatility). Bypassing it allows a swap to settle at a price the admin explicitly deemed too risky. LPs bear the loss: they provide liquidity at the manipulated/stale price while the swapper extracts value. This is a direct loss of LP principal, matching the "swap conservation failure / bad-price execution" impact class.

---

### Likelihood Explanation

The attack requires no special privilege, no token balance beyond normal swap collateral, and no complex setup. Any EOA can call `PriceVelocityGuardExtension.beforeSwap` directly in the same block as a swap. The only precondition is that the oracle price has moved enough to trigger the guard — exactly the condition under which the guard is most valuable. Likelihood is high whenever the guard is active and the oracle price moves significantly.

---

### Recommendation

Add the `onlyPool` modifier to `PriceVelocityGuardExtension.beforeSwap`, consistent with the base class declaration:

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
) external override onlyPool returns (bytes4) {   // <-- add onlyPool
    ...
}
```

`onlyPool` checks `IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)`, which returns `true` only for factory-registered pools: [6](#0-5) [7](#0-6) 

---

### Proof of Concept

```
// Assume: pool P has PriceVelocityGuardExtension E configured with maxChangePerBlockE18 = 5e16 (5%)
// Oracle price has moved 20% in one block — guard would block the swap.

// Step 1: Attacker calls the extension directly (not through the pool)
//         with bid/ask that produce midPrice = currentOracleMid
E.beforeSwap(
    attacker,          // sender (ignored)
    attacker,          // recipient (ignored)
    true,              // zeroForOne (ignored)
    0,                 // amountSpecified (ignored)
    0,                 // priceLimitX64 (ignored)
    0,                 // packedSlot0 (ignored)
    currentOracleMid - 1,   // bidPriceX64
    currentOracleMid + 1,   // askPriceX64
    ""
);
// Result: priceVelocityState[P].lastMidPriceX64 = currentOracleMid
//         priceVelocityState[P].lastUpdateBlock  = block.number

// Step 2: Attacker calls pool.swap() in the same block
//         Pool calls E.beforeSwap() with oracle prices → midPrice = currentOracleMid
//         delta = |currentOracleMid - currentOracleMid| = 0
//         actualSq = 0 ≤ allowedSq → guard passes
//         Swap executes at the 20%-moved price, extracting value from LPs.
P.swap(recipient, true, amountIn, 0, callbackData, "");
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-58)
```text
    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L149-151)
```text
  function isPool(address pool) external view override returns (bool) {
    return poolToIdx[pool] != 0;
  }
```
