### Title
`getTime()` Returns Maximum of Two Market Timestamps, Creating False Freshness and Incorrect Order Expiry - (`File: core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.getTime()` returns `max(spotTime, perpTime)` instead of `min(spotTime, perpTime)`. This is a direct structural analog of the `EACAggregatorCombine.latestTimestamp()` vulnerability: when one market's tick stream lags behind the other, the protocol's oracle time is inflated to the more-recent stream, masking the staleness of the lagging market. Every consumer of `getOracleTime()` — including order expiration checks in `OffchainExchange._expired()` — operates as if both markets are as fresh as the most recently updated one.

---

### Finding Description

`Endpoint.getTime()` combines two independently-updated timestamps:

```solidity
// core/contracts/Endpoint.sol
function getTime() external view returns (uint128) {
    Times memory t = times;
    uint128 _time = t.spotTime > t.perpTime ? t.spotTime : t.perpTime;
    require(_time != 0, ERR_INVALID_TIME);
    return _time;
}
```

`spotTime` and `perpTime` are updated by separate sequencer-submitted tick transactions (`SpotTick` and `PerpTick`). If one stream is delayed — e.g., no perp activity for an extended period — `getTime()` still returns the other stream's (newer) time, presenting an inflated oracle time to all callers.

`EndpointGated.getOracleTime()` wraps this call:

```solidity
// core/contracts/EndpointGated.sol
function getOracleTime() internal view returns (uint128) {
    return IEndpoint(endpoint).getTime();
}
```

`OffchainExchange._expired()` uses `getOracleTime()` to gate order execution:

```solidity
// core/contracts/OffchainExchange.sol
function _expired(uint64 expiration) internal view returns (bool) {
    return expiration <= getOracleTime();
}
```

`getOracleTime()` is also consumed in `SpotEngine.sol` (4 call sites) and `SpotEngineState.sol` (2 call sites) for time-dependent state updates such as interest accrual.

---

### Impact Explanation

**Order expiration desynchronization:** Suppose `perpTime` is 2 hours ahead of `spotTime` (e.g., perp ticks are frequent but spot ticks are delayed). `getTime()` returns `perpTime`. A spot order with `expiration = perpTime − 30 min` satisfies `expiration <= getOracleTime()` and is treated as expired — even though `spotTime` has not yet reached that expiration. Valid, in-window spot orders are incorrectly rejected.

The reverse also holds: if `spotTime` is ahead of `perpTime`, perp orders that should still be live are incorrectly expired.

**State update skew:** `SpotEngine` and `SpotEngineState` use `getOracleTime()` for time-delta calculations (interest, funding). If `getTime()` returns `perpTime` when `spotTime` is stale, the spot engine accrues interest over an inflated time delta, corrupting per-product balance accounting.

---

### Likelihood Explanation

The two tick streams are updated independently by the sequencer. Any period of low activity in one market (spot or perp) causes the streams to diverge. This is a normal operational condition, not an edge case. No attacker action is required — the divergence occurs organically and affects all users whose orders straddle the gap between the two timestamps.

---

### Recommendation

Replace the `max` selection with `min` so that `getTime()` reflects the staleness of the least-recently-updated market:

```solidity
uint128 _time = t.spotTime < t.perpTime ? t.spotTime : t.perpTime;
```

Alternatively, maintain separate `getSpotTime()` and `getPerpTime()` accessors and route each engine's expiration and state-update logic to its own canonical time, eliminating cross-market timestamp contamination.

---

### Proof of Concept

1. Sequencer submits `SpotTick` with `time = T`. `spotTime = T`.
2. No `PerpTick` is submitted for 2 hours. `perpTime` remains at `T − 2h`.
3. `getTime()` returns `max(T, T−2h) = T`.
4. A perp order is submitted with `expiration = T − 1h`.
5. `_expired(T − 1h)` evaluates `(T − 1h) <= T` → `true` → order is rejected as expired.
6. However, `perpTime = T − 2h`, so from the perp market's perspective the order's expiration has not been reached yet (`T − 1h > T − 2h`). The order was valid and should have been matched. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/Endpoint.sol (L344-349)
```text
    function getTime() external view returns (uint128) {
        Times memory t = times;
        uint128 _time = t.spotTime > t.perpTime ? t.spotTime : t.perpTime;
        require(_time != 0, ERR_INVALID_TIME);
        return _time;
    }
```

**File:** core/contracts/EndpointGated.sol (L21-23)
```text
    function getOracleTime() internal view returns (uint128) {
        return IEndpoint(endpoint).getTime();
    }
```

**File:** core/contracts/OffchainExchange.sol (L345-347)
```text
    function _expired(uint64 expiration) internal view returns (bool) {
        return expiration <= getOracleTime();
    }
```

**File:** core/contracts/EndpointTx.sol (L471-485)
```text
            Times memory t = times;
            uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
            spotEngine.updateStates(dt);
            t.spotTime = txn.time;
            times = t;
        } else if (txType == IEndpoint.TransactionType.PerpTick) {
            IEndpoint.PerpTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.PerpTick)
            );
            Times memory t = times;
            uint128 dt = t.perpTime == 0 ? 0 : txn.time - t.perpTime;
            perpEngine.updateStates(dt, txn.avgPriceDiffs);
            t.perpTime = txn.time;
            times = t;
```
