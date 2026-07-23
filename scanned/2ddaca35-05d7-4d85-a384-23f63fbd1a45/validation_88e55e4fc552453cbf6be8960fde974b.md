### Title
Missing Sequencer Uptime Check in L2 Price Providers Allows Stale-Price Exploitation via Delayed Inbox — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` perform a time-delta staleness check (`MAX_TIME_DELTA`) but contain no Chainlink Sequencer Uptime Feed check and no minimum post-recovery uptime period. When an L2 sequencer (e.g., Arbitrum) resumes after downtime shorter than `MAX_TIME_DELTA`, the last on-chain oracle price — published before the outage — passes the staleness gate and is fed directly into pool swaps. An attacker who pre-submitted a swap via Arbitrum's delayed inbox can execute against this stale bid/ask, draining LP principal.

---

### Finding Description

Both L2 price providers delegate staleness detection entirely to `_isStale()`:

```solidity
// PriceProviderL2.sol lines 135-150
function _isStale(
    uint256 refTime,
    uint256 nowTs,
    uint256 maxDelta,
    uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;
    }
    return (nowTs - refTime) > maxDelta;
}
```

`MAX_TIME_DELTA` is configurable up to 7 days at construction time. If the sequencer outage lasts less than `MAX_TIME_DELTA` (e.g., 30 minutes with a 1-hour `MAX_TIME_DELTA`), the pre-downtime oracle price — whose `refTime` is only 30 minutes old — passes the check and is accepted as valid. Neither contract queries a Chainlink Sequencer Uptime Feed, nor enforces that the sequencer has been live for at least the duration of the observation window before accepting oracle data.

The `FUTURE_TOLERANCE` immutable addresses only clock-skew (oracle `refTime` slightly ahead of `block.timestamp`); it provides no protection against the post-downtime stale-price window.

---

### Impact Explanation

`MetricOmmPool._getBidAndAskPriceX64()` calls `IPriceProvider(activePriceProvider).getBidAndAskPrice()` on every swap. The returned `bid`/`ask` directly set the mid-price and spread fee used by `SwapMath` to price every bin traversal. A stale bid/ask that reflects the pre-downtime market price causes the pool to execute swaps at the wrong price, transferring value from LPs to the attacker.

**Concrete scenario (Arbitrum, ETH/USDC pool):**
1. Oracle price on-chain: $2,000. `MAX_TIME_DELTA` = 1 hour.
2. Sequencer goes offline for 30 minutes. ETH rises to $2,200 off-chain.
3. Attacker submits "buy ETH" (sell USDC → ETH) via Arbitrum's delayed inbox during downtime.
4. Sequencer resumes. Last Pyth price on-chain has `refTime` = 30 min ago. `nowTs − refTime` = 30 min < 1 hour → staleness check passes.
5. Attacker's delayed-inbox transaction executes at the stale $2,000 oracle price.
6. Attacker acquires ETH worth $2,200 for $2,000, extracting $200/ETH from LP reserves.

For a pool with $1 M in ETH liquidity and a 10% price move, the attacker can extract ~$100 K in a single transaction. The loss is direct LP principal — `binTotals.scaledToken0` is decremented by the full swap output while the pool receives only the stale-price-equivalent input.

---

### Likelihood Explanation

- Arbitrum sequencer outages are rare but documented (multiple incidents in 2023–2024).
- The delayed inbox is a standard, permissionless Arbitrum feature; no special privilege is required.
- The attacker only needs to observe off-chain price movement during downtime (publicly visible on CEXs) and submit a directional swap before the sequencer resumes.
- The attack succeeds whenever downtime duration < `MAX_TIME_DELTA`, which is the common case for short outages with a permissive staleness window.
- Likelihood: **Low-Medium** (requires sequencer downtime + directional price move + attacker awareness), but the impact when triggered is **High**.

---

### Recommendation

1. **Integrate Chainlink's Sequencer Uptime Feed** in both `PriceProviderL2` and `ProtectedPriceProviderL2`. Revert (return stale sentinel) if the sequencer is reported as down.
2. **Enforce a minimum post-recovery grace period** (e.g., 1 hour) after the sequencer comes back online before accepting oracle data. Store the sequencer-up timestamp and require `block.timestamp − sequencerUpAt >= GRACE_PERIOD`.
3. **Tighten `MAX_TIME_DELTA`** for L2 deployments to a value shorter than the shortest expected sequencer outage (e.g., 5–10 minutes), so that any meaningful downtime automatically invalidates the cached oracle price.

---

### Proof of Concept

```
Setup:
  - Pool on Arbitrum with PriceProviderL2, MAX_TIME_DELTA = 3600 (1 hour)
  - ETH/USDC pool, oracle mid = $2000, pool holds 500 ETH

Attack:
  T=0:   Sequencer goes offline. Last Pyth push: refTime = T=0, price = $2000.
  T=0:   ETH rises to $2200 on CEXs (attacker observes this off-chain).
  T=0+ε: Attacker submits swap via Arbitrum delayed inbox:
           pool.swap(attacker, false /*USDC→ETH*/, +440_000e6 /*exact USDC in*/, 0, ...)
  T=1800: Sequencer resumes. block.timestamp = T=1800.
           _isStale(refTime=0, nowTs=1800, maxDelta=3600, futureTol=...) 
           → (1800 - 0) = 1800 < 3600 → NOT stale → price accepted as $2000.
  T=1800: Delayed inbox tx executes. Pool prices swap at $2000 oracle.
           Attacker receives 220 ETH (worth $2200 × 220 = $484,000) for $440,000 USDC.
           LP loss: $44,000 (10% of swap value).

  After: Oracle keepers push new price ($2200). Pool now correctly priced,
         but LP funds already extracted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L34-38)
```text
    uint256 public immutable MAX_TIME_DELTA;

    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L92-96)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L135-150)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta,
        uint256 futureTol
    ) internal pure returns (bool) {
        if (refTime == 0) return true;

        if (refTime > nowTs) {
            // refTime in the future: tolerate only within futureTol
            return (refTime - nowTs) > futureTol;
        }

        // refTime in the past or equal: check age
        return (nowTs - refTime) > maxDelta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-217)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L138-153)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta,
        uint256 futureTol
    ) internal pure returns (bool) {
        if (refTime == 0) return true;

        if (refTime > nowTs) {
            // refTime in the future: tolerate only within futureTol
            return (refTime - nowTs) > futureTol;
        }

        // refTime in the past or equal: check age
        return (nowTs - refTime) > maxDelta;
    }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L196-209)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
        return _computeBidAsk(mid, spread, refTime);
    }

    /// @dev Downstream pricing: staleness, price guard, confidence spread, marginStep.
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```
