### Title
Missing Sequencer Uptime Grace-Period Check in L2 Price Providers Allows Bad-Price Execution After Sequencer Restart — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are explicitly designed for L2 deployment and implement an L2-specific `FUTURE_TOLERANCE` to handle sequencer clock skew. However, neither contract implements a Chainlink sequencer uptime feed check or enforces a grace period after sequencer restart. After a sequencer outage, swaps resume immediately at oracle prices that may not yet reflect true market conditions, enabling arbitrageurs to drain LP assets at bad prices.

---

### Finding Description

Both L2 price providers implement a staleness check that correctly rejects oracle data older than `MAX_TIME_DELTA`:

```solidity
// PriceProviderL2.sol lines 135–150
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool)
{
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;
    }
    return (nowTs - refTime) > maxDelta;
}
``` [1](#0-0) 

This staleness check correctly blocks swaps *during* sequencer downtime (oracle data ages past `MAX_TIME_DELTA`). However, the moment the sequencer restarts and a fresh oracle update is published, `_isStale` returns `false` and `_getBidAndAskPrice` proceeds immediately:

```solidity
// PriceProviderL2.sol lines 208–217
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spread, , uint256 refTime) =
        IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

    // 2. Staleness check
    if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
        return (0, type(uint128).max);
    }
    ...
``` [2](#0-1) 

There is no check against a Chainlink sequencer uptime feed and no `GRACE_PERIOD` enforcement after the sequencer comes back online. The identical gap exists in `ProtectedPriceProviderL2._computeBidAsk`: [3](#0-2) 

The L1 variants (`PriceProvider.sol`, `ProtectedPriceProvider.sol`) are not affected because sequencer downtime is an L2-specific concern. [4](#0-3) 

---

### Impact Explanation

**Impact: Medium**

During sequencer downtime, L1 transactions queue up. When the sequencer restarts, these queued transactions are processed in order. An attacker who observed the pre-downtime oracle price and knows the market moved significantly during the outage can:

1. Submit a swap transaction on L1 during downtime (it queues).
2. Wait for the sequencer to restart and the first oracle update to land (staleness check now passes).
3. Their queued swap executes immediately at the first post-restart oracle price, before the market has fully settled.

Because the pool's bid/ask is derived directly from the oracle price with no grace-period gate, the pool's LPs are exposed to arbitrage at a price that does not reflect the true post-downtime market equilibrium. LP principal (token reserves) is the directly impacted asset. This matches the allowed impact gate: **bad-price execution** and **LP asset loss**.

---

### Likelihood Explanation

**Likelihood: Low**

Sequencer downtime is an infrequent but real event on production L2 networks (Arbitrum, Optimism, Base). The attack requires timing a queued transaction around a sequencer restart, which is opportunistic but not complex. The likelihood is low but non-zero, and the protocol explicitly deploys L2-specific contracts, confirming L2 deployment is in scope.

---

### Recommendation

Add a Chainlink sequencer uptime feed check with a configurable `GRACE_PERIOD` (e.g., 3600 seconds) to both `PriceProviderL2` and `ProtectedPriceProviderL2`, following the [Chainlink L2 sequencer feed documentation](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code):

```solidity
// Pseudocode addition to _getBidAndAskPrice() / _computeBidAsk()
(, int256 answer, uint256 startedAt, ,) = sequencerUptimeFeed.latestRoundData();
bool isSequencerUp = (answer == 0);
if (!isSequencerUp) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

Store `sequencerUptimeFeed` and `GRACE_PERIOD` as immutables set at construction time, alongside the existing `FUTURE_TOLERANCE` and `MAX_TIME_DELTA`.

---

### Proof of Concept

1. Pool is deployed on an L2 (e.g., Arbitrum) using `PriceProviderL2` with `MAX_TIME_DELTA = 3600` (1 hour).
2. The L2 sequencer goes offline. Oracle data ages; after 1 hour, `_isStale` returns `true` and all swaps revert with `FeedStalled`.
3. During downtime, ETH/USD moves from $3000 to $3500 on L1 markets.
4. Attacker submits a buy-ETH swap transaction on L1 (it queues in the sequencer inbox).
5. Sequencer restarts. The oracle publishes a fresh price of $3500. `_isStale` now returns `false`.
6. Attacker's queued transaction executes: they buy ETH from the pool at the first post-restart oracle price. The pool's LPs, who provided liquidity at $3000-level bins, absorb the loss — their token0 reserves are depleted at a price that does not reflect the $500 move that occurred during downtime.
7. No grace period gate exists to block this window; the pool resumes trading the instant `refTime` is fresh.

### Citations

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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L203-215)
```text
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }

        // 2. Basic validity — price must be positive, spread must not be stalled marker
        if (price == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L124-133)
```text
    /// @dev Pure staleness check (L1). Any future refTime is stale.
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
```
