### Title
`block.number` Returns L1 Block Number on Arbitrum, Miscalibrating the Price Velocity Guard - (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`PriceVelocityGuardExtension` uses `block.number` at three points to record and compare block positions for its velocity-cap formula. On Arbitrum, `block.number` returns the approximate L1 block number (one block every ~12 s) rather than the L2 block number (one block every ~0.25 s). Because the guard's allowed-change window scales as `sqrt(1 + blockDiff)`, using L1 block numbers makes `blockDiff` grow ~48× slower than the actual number of L2 blocks elapsed, rendering the guard ~7× more restrictive than the pool admin configured it to be. Swaps that should be permitted under the admin's intended L2-block calibration are incorrectly reverted, breaking core pool swap functionality.

### Finding Description

**Root cause — three uses of `block.number` in `PriceVelocityGuardExtension.sol`:**

```
Line 32:  s.lastUpdateBlock = uint64(block.number);   // setLastMidPrice
Line 58:  s.lastUpdateBlock = uint64(block.number);   // beforeSwap – state write
Line 63:  uint256 blockDiff = block.number - prevBlock; // beforeSwap – guard check
``` [1](#0-0) 

The guard enforces:

```
changeE18² ≤ maxChangePerBlockE18² × (1 + blockDiff)
```

where `blockDiff = block.number − prevBlock`.

**On Arbitrum**, per the chain's documentation, `block.number` returns the L1 block number at which the sequencer received the transaction, not the current L2 block number. L1 blocks arrive every ~12 s; L2 blocks arrive every ~0.25 s — a ratio of ~48.

**Concrete miscalibration:**

| Elapsed real time | L2 blocks elapsed | L1 blocks elapsed (`block.number` delta) | Allowed change (L2-calibrated) | Allowed change (actual, L1 blocks) |
|---|---|---|---|---|
| 0.25 s | 1 | 0 | `1.0 × maxChange` | `1.0 × maxChange` |
| 12 s | 48 | 1 | `√49 ≈ 7.0 × maxChange` | `√2 ≈ 1.41 × maxChange` |
| 60 s | 240 | 5 | `√241 ≈ 15.5 × maxChange` | `√6 ≈ 2.45 × maxChange` |

A pool admin who sets `maxChangePerBlockE18` assuming L2 blocks (the natural assumption on Arbitrum) will find the guard ~5–7× more restrictive than intended for swaps separated by one or more L1 blocks. Swaps that move the mid-price by more than `√2 × maxChange` within a single L1 block (~12 s) are reverted with `PriceVelocityExceeded`, even though the admin intended to allow up to `7 × maxChange` over that same real-time window. [2](#0-1) 

**Code path:**

1. `MetricOmmPool.swap` calls `_beforeSwap` → `ExtensionCalling` dispatches to `PriceVelocityGuardExtension.beforeSwap`.
2. `beforeSwap` reads `s.lastUpdateBlock` (stored as an L1 block number on Arbitrum) and computes `blockDiff = block.number − prevBlock` (also L1).
3. The guard compares `actualSq` against `allowedSq = maxChange² × (1 + blockDiff_L1)`.
4. Because `blockDiff_L1 ≪ blockDiff_L2`, `allowedSq` is far smaller than the admin intended, and the revert fires on legitimate swaps. [3](#0-2) 

### Impact Explanation

- **Broken core pool swap functionality**: Any swap that moves the mid-price by more than `√(1 + blockDiff_L1) × maxChangePerBlockE18` is reverted. On Arbitrum with a typical L2-calibrated `maxChangePerBlockE18`, this threshold is ~7× lower than intended, blocking the vast majority of legitimate arbitrage and user swaps within a single L1 block window.
- **LP principal at risk**: With arbitrage blocked, the pool's mid-price diverges from the true market price. LPs are exposed to adverse selection — informed traders can wait for the guard to relax (after enough L1 blocks) and then execute a large price-moving swap in a single step, extracting value from LPs at the stale price.
- **Unusable swap flow**: The pool effectively becomes non-functional for normal market-making on Arbitrum whenever the oracle price moves faster than the artificially tight L1-block-based cap.

Severity: **Medium** — broken core swap functionality with indirect LP principal loss through stale-price adverse selection.

### Likelihood Explanation

- The protocol is a general-purpose AMM extension framework with no chain restriction in scope; Arbitrum is a primary EVM L2 deployment target.
- Any pool admin who configures `maxChangePerBlockE18` using L2-block intuition (the natural and documented behavior on Arbitrum) will trigger this miscalibration without any privileged or malicious action.
- No attacker capability is required; the miscalibration is automatic on deployment to Arbitrum.

### Recommendation

Replace `block.number` with the Arbitrum L2 block number via `ArbSys(address(100)).arbBlockNumber()` when deployed on Arbitrum, or use a chain-agnostic abstraction:

```solidity
// Option A: Arbitrum-specific
uint256 blockNum = block.chainid == 42161 || block.chainid == 421614
    ? ArbSys(address(100)).arbBlockNumber()
    : block.number;

// Option B: Use block.timestamp instead of block.number
// Replace blockDiff (in blocks) with a time-based delta (in seconds),
// and rename maxChangePerBlockE18 → maxChangePerSecondE18.
```

Using `block.timestamp` is the most portable fix: it is accurate on all EVM chains and avoids the L1/L2 block-number ambiguity entirely. The guard formula becomes:

```
changeE18² ≤ maxChangePerSecondE18² × (1 + timeDelta)
``` [4](#0-3) 

### Proof of Concept

**Setup (Arbitrum fork):**

1. Deploy `MetricOmmPoolFactory` + `PriceVelocityGuardExtension` on an Arbitrum fork.
2. Create a pool with the extension enabled for `beforeSwap`.
3. Pool admin calls `setMaxChangePerBlock(pool, 1e16)` — intending to allow 1% price movement per L2 block (0.25 s).
4. Pool admin calls `setLastMidPrice(pool, initialMid)` — records `lastUpdateBlock = block.number` (L1 block N).

**Attack / demonstration:**

5. Wait for 48 L2 blocks (~12 s, 1 L1 block). `block.number` is now N+1.
6. Oracle price moves 5% (realistic for a volatile pair over 12 s).
7. Arbitrageur calls `pool.swap(...)` with a trade that moves mid-price by 5%.
8. In `beforeSwap`:
   - `blockDiff = (N+1) − N = 1`
   - `allowedSq = (1e16)² × 2 = 2e32` → `sqrt(allowedSq) ≈ 1.41%`
   - `actualSq = (5e16)² = 25e32`
   - `25e32 > 2e32` → **`PriceVelocityExceeded` revert**
9. The swap is blocked even though 48 L2 blocks (12 s) have elapsed and the admin intended to allow `sqrt(49) × 1% ≈ 7%` movement over that window.
10. Pool price remains stale at the pre-move level; LPs are exposed to adverse selection until enough L1 blocks accumulate to widen the guard. [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
