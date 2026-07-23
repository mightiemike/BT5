### Title
Missing Arbitrum Sequencer Uptime Check in L2 Price Providers Allows Stale-Price Swaps - (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are explicitly designed for L2 deployment (they carry a `FUTURE_TOLERANCE` immutable to handle sequencer clock skew) but contain no check against a Chainlink L2 Sequencer Uptime Feed. Their only liveness guard is a `_isStale()` comparison of the oracle's `refTime` against `block.timestamp`. When the Arbitrum sequencer restarts after a downtime shorter than `MAX_TIME_DELTA`, the stale pre-downtime price passes the staleness check and is returned as a valid bid/ask to the pool, enabling swaps at prices that no longer reflect the true market.

---

### Finding Description

Both L2 price providers share the same staleness logic:

```solidity
// PriceProviderL2.sol lines 135–150
function _isStale(
    uint256 refTime, uint256 nowTs,
    uint256 maxDelta, uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;
    }
    return (nowTs - refTime) > maxDelta;
}
``` [1](#0-0) 

This check compares the oracle's published `refTime` against `block.timestamp`. It does **not** consult a sequencer uptime feed.

The attack window opens as follows:

1. The Arbitrum sequencer goes offline. `block.timestamp` on L2 freezes at the last produced block's timestamp (call it `T`). The oracle's last `refTime` is `T − δ` (a few seconds before the outage).
2. During the outage, the true market price moves significantly on L1 (e.g., a de-peg event, a large price drop).
3. The sequencer restarts at `T + D` (where `D` is the downtime duration). `block.timestamp` jumps to `T + D`.
4. The oracle has not yet been updated; `refTime` is still `T − δ`.
5. The staleness check evaluates `(T + D) − (T − δ) = D + δ`. If `D + δ ≤ MAX_TIME_DELTA`, the check passes and the stale pre-outage price is returned.

`MAX_TIME_DELTA` is configurable up to **7 days** at deployment:

```solidity
// PriceProviderL2.sol line 92
if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
``` [2](#0-1) 

A provider deployed with `MAX_TIME_DELTA = 24 hours` would accept a stale price from a 23-hour outage. Even a tightly configured provider (e.g., `MAX_TIME_DELTA = 1 hour`) is vulnerable to any outage shorter than that threshold.

The stale price then flows through `_getBidAndAskPrice()` → `getBidAndAskPrice()` → the pool's swap execution without any sequencer-liveness gate:

```solidity
// PriceProviderL2.sol lines 208–217
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spread, , uint256 refTime) =
        IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
    if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
        return (0, type(uint128).max);
    }
    ...
``` [3](#0-2) 

The identical gap exists in `ProtectedPriceProviderL2._computeBidAsk()`:

```solidity
// ProtectedPriceProviderL2.sol lines 203–209
function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
    internal view returns (uint128, uint128)
{
    if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
        return (0, type(uint128).max);
    }
``` [4](#0-3) 

Neither contract queries a Chainlink Sequencer Uptime Feed (e.g., `0xFdB631F5EE196F0ed6FAa767959853A9F217697D` on Arbitrum) nor enforces a post-restart grace period.

The `FUTURE_TOLERANCE` immutable, while L2-aware, addresses only clock skew (oracle `refTime` slightly ahead of `block.timestamp`); it does not address sequencer liveness:

```solidity
/// @dev L2 sequencer timestamp can lag behind oracle publication time.
///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
uint256 public immutable FUTURE_TOLERANCE;
``` [5](#0-4) 

---

### Impact Explanation

A swap executed against the pool during the post-restart stale-price window uses a bid/ask derived from the pre-outage oracle price. If the true market price has moved (e.g., ETH dropped 15% during a 45-minute outage), an attacker can:

- **Buy the underpriced token** from the pool at the stale ask (below true market ask).
- **Sell the overpriced token** to the pool at the stale bid (above true market bid).

LP principal is directly extracted at the magnitude of the price deviation times the swap volume. This is a **bad-price execution** impact: stale bid/ask quotes reach pool swaps, causing direct loss of LP assets. The loss is unbounded by any protocol cap and scales with pool depth and price movement during the outage.

---

### Likelihood Explanation

- The protocol explicitly targets L2 deployment (dedicated `PriceProviderL2`, `ProtectedPriceProviderL2`, `PriceProviderFactoryL2` contracts).
- Arbitrum sequencer outages are documented historical events.
- The trigger requires no special privilege: any user can submit a swap immediately after sequencer restart.
- The vulnerability window persists until the oracle is updated with a fresh price, which can take minutes to hours depending on the oracle backend and update frequency.
- `MAX_TIME_DELTA` up to 7 days means even a very long outage may not trigger the staleness guard.

---

### Recommendation

Add a sequencer uptime check at the top of `_getBidAndAskPrice()` / `_computeBidAsk()` in both `PriceProviderL2` and `ProtectedPriceProviderL2`:

```solidity
// Store as an immutable set at construction:
AggregatorV2V3Interface public immutable sequencerUptimeFeed;
uint256 public immutable GRACE_PERIOD; // e.g., 3600 seconds

function _checkSequencer() internal view {
    (, int256 answer, uint256 startedAt, ,) = sequencerUptimeFeed.latestRoundData();
    // answer == 0 means sequencer is up; 1 means down
    if (answer != 0) revert SequencerDown();
    // Enforce grace period after restart
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotElapsed();
}
```

Call `_checkSequencer()` before the staleness check in `_getBidAndAskPrice()`. This mirrors the Chainlink-recommended pattern for L2 deployments and closes the stale-price window that opens after sequencer restart.

---

### Proof of Concept

1. Deploy `PriceProviderL2` on Arbitrum with `MAX_TIME_DELTA = 3600` (1 hour) and a Chainlink or Pyth feed.
2. Simulate sequencer downtime: freeze `block.timestamp` at `T`, oracle `refTime` at `T − 30s`.
3. Advance real time by 45 minutes (downtime = 45 min < MAX_TIME_DELTA = 60 min). During this time, the true ETH price drops 10%.
4. Restart sequencer: `block.timestamp` jumps to `T + 2700`.
5. Call `getBidAndAskPrice()` before the oracle is updated.
6. `_isStale` evaluates `(T + 2700) − (T − 30) = 2730 ≤ 3600` → **not stale**, returns pre-outage price.
7. Execute a swap on the pool using the stale price. The pool sells ETH at the pre-outage (10% higher) ask price. The attacker receives ETH at a 10% discount; LPs absorb the loss.

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L36-38)
```text
    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L92-92)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L203-209)
```text
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```
