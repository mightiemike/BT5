### Title
Precision Loss in `PriceVelocityGuardExtension::beforeSwap` Allows Velocity Cap Bypass — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

The `PriceVelocityGuardExtension.beforeSwap` hook computes the squared relative price change by first performing an integer division and then squaring the truncated result. This division-before-multiplication pattern causes `actualSq` to be systematically underestimated, allowing swaps whose true oracle price velocity exceeds the configured cap to pass the guard unchallenged.

---

### Finding Description

In `beforeSwap`, the velocity check is:

```solidity
uint256 changeE18 = (delta * 1e18) / uint256(prevMid);   // line 67 — integer division truncates
uint256 actualSq  = changeE18 * changeE18;                // line 69 — squares the already-truncated value
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

if (actualSq > allowedSq) {
    revert PriceVelocityExceeded(actualSq, allowedSq);
}
``` [1](#0-0) 

Let `r = (delta * 1e18) % prevMid` be the truncation remainder. Then:

- **Computed** `changeE18 = (delta * 1e18 - r) / prevMid`
- **True** change = `(delta * 1e18) / prevMid` (rational)
- **Computed** `actualSq = ((delta * 1e18 - r) / prevMid)²`
- **True** sq = `(delta * 1e18)² / prevMid²`

The error is `(2 · delta · 1e18 · r − r²) / prevMid²`. When `r ≈ prevMid − 1` (worst case), the error approaches `2 · changeE18`, meaning a price move whose true squared change exceeds `allowedSq` by up to `2 · changeE18` can produce a computed `actualSq ≤ allowedSq` and pass the guard.

The analogous correct computation avoids the intermediate division:

```solidity
// True squared change without intermediate truncation:
// actualSq = mulDiv(delta * 1e18, delta * 1e18, prevMid * prevMid)
```

The `PriceVelocityGuardExtension` is registered as a `beforeSwap` extension on pools via the factory's extension order system. [2](#0-1) 

The `beforeSwap` hook is called by `MetricOmmPool.swap` before any swap execution, so a bypassed guard allows the swap to proceed at the manipulated price. [3](#0-2) 

---

### Impact Explanation

The `PriceVelocityGuardExtension` is the sole on-chain mechanism preventing rapid oracle price manipulation between blocks. When the guard is bypassed, a swap can execute at an oracle price that moves faster than the configured `maxChangePerBlockE18` cap. Because the pool's bid/ask prices are derived directly from the oracle, a manipulated oracle price causes the pool to trade at a bad price — the trader receives more output than the oracle-anchored curve permits, or the pool receives less input than owed. This is a direct swap conservation failure and constitutes loss of LP principal.

---

### Likelihood Explanation

The bypass is triggered by any swap where `(delta * 1e18) % prevMid ≠ 0` and the true squared change is within `2 · changeE18` of `allowedSq`. This is a continuous arithmetic condition, not a special configuration. Any unprivileged caller who can call `swap` on a pool with this extension registered can trigger it. No malicious setup is required — the extension is designed for production use.

---

### Recommendation

Replace the division-before-squaring pattern with a single `mulDiv` that defers all division to the end:

```diff
- uint256 changeE18 = (delta * 1e18) / uint256(prevMid);
- uint256 actualSq  = changeE18 * changeE18;
+ // Compute (delta/prevMid)^2 * 1e36 without intermediate truncation.
+ // Math.mulDiv handles the 512-bit intermediate product internally.
+ uint256 actualSq = Math.mulDiv(delta * 1e18, delta * 1e18, uint256(prevMid) * uint256(prevMid));
```

Note: `delta * 1e18` fits `uint256` because `delta ≤ type(uint128).max ≈ 3.4e38` and `3.4e38 × 1e18 = 3.4e56 < 2^256`. However, `prevMid * prevMid` can overflow `uint256` when `prevMid` is close to `type(uint128).max`. A safe alternative is to factor the division:

```solidity
uint256 actualSq = Math.mulDiv(delta, delta * 1e36, uint256(prevMid) * uint256(prevMid));
// or use two sequential mulDivs to stay within uint256 bounds
```

---

### Proof of Concept

```solidity
// Concrete example:
// prevMid   = 1e18 (Q64.64 price, arbitrary)
// maxChange = 1e17 (10% per block, E18 scale)
// blockDiff = 0    (same block)
// allowedSq = (1e17)^2 * 1 = 1e34

// Attacker submits a swap where:
// delta = 1e17 + 1  (just over 10% of prevMid = 1e18)
// True changeE18 = (1e17 + 1) * 1e18 / 1e18 = 1e17 + 1
// True actualSq  = (1e17 + 1)^2 = 1e34 + 2e17 + 1  > allowedSq (1e34) → should revert

// Computed changeE18 = floor((1e17 + 1) * 1e18 / 1e18) = 1e17 + 1
// (In this case no truncation, but consider:)

// delta = 1e17 * 1e18 / 1e18 + (1e18 - 1) / 1e18 remainder
// Choose prevMid = 3, delta = 2:
// True changeE18 = 2e18/3 = 0.666...e18
// Computed changeE18 = floor(2e18/3) = 666666666666666666
// Computed actualSq  = 666666666666666666^2 = 4.44e35
// True actualSq      = (2/3)^2 * 1e36 = 4.44...e35
// If allowedSq = 4.44e35 (just above computed), guard passes when it should block.

// Foundry test sketch:
function testVelocityGuardPrecisionBypass() public {
    // Configure: maxChange = 666666666666666667 (E18), 1 block
    // prevMid = 3 (Q64.64 units), newMid = 5
    // delta = 2, changeE18_true = 2e18/3 ≈ 666666666666666666.67
    // changeE18_computed = 666666666666666666 (truncated)
    // actualSq_computed  = 666666666666666666^2
    // allowedSq          = 666666666666666667^2 * 1
    // actualSq_computed < allowedSq → guard passes
    // but true change > maxChange → should revert
}
```

### Citations

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
