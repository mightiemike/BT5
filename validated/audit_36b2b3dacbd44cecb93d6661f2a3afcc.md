### Title
`getTime()` Returns Newest of Two Engine Timestamps, Creating False Sense of Protocol Freshness — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.getTime()` returns `max(spotTime, perpTime)` instead of `min(spotTime, perpTime)`. If one engine's tick has not been submitted by the sequencer for an extended period, the function still reports the newer engine's timestamp as the protocol's current time, masking the staleness of the lagging engine.

---

### Finding Description

The `Times` struct in `EndpointStorage.sol` tracks two independent engine clocks: [1](#0-0) 

Each clock is advanced independently by sequencer-submitted `SpotTick` and `PerpTick` transactions: [2](#0-1) 

The public `getTime()` function combines them by returning the **newer** of the two: [3](#0-2) 

This is structurally identical to the audited `EACAggregatorCombine.latestTimestamp()` pattern: when one source is stale, the function returns the fresher source's timestamp, giving callers no signal that one engine clock has fallen behind.

---

### Impact Explanation

`getTime()` is part of the public `IEndpoint` interface and is called by `EndpointGated.sol`: [4](#0-3) 

Any on-chain or off-chain consumer of `getTime()` — including external integrators, keeper bots, or protocol components checking whether the protocol's internal clock is current — will receive a misleading value. If `perpTime` has not been advanced for hours or days while `spotTime` is current, `getTime()` returns `spotTime`, signaling that the protocol is live and up-to-date when the perp engine's state is actually stale. This can cause:

- External integrators to treat stale perp-engine state as fresh when making decisions (e.g., health checks, liquidation timing, NLP pricing).
- Protocol-level time-gated logic that relies on `getTime()` to operate against an incorrect clock, potentially allowing actions that should be blocked during a perp-engine outage.

---

### Likelihood Explanation

The sequencer submits `SpotTick` and `PerpTick` as separate transaction types. A partial sequencer degradation, a deliberate sequencer omission of one tick type, or a chain reorganization affecting only one tick stream can cause `spotTime` and `perpTime` to diverge. This is a realistic operational scenario, not a theoretical one.

---

### Recommendation

Change `getTime()` to return the **oldest** (minimum) of the two timestamps, so that the reported protocol time only advances when both engines have been ticked:

```solidity
function getTime() external view returns (uint128) {
    Times memory t = times;
    // Return the older timestamp so staleness in either engine is surfaced
    uint128 _time = t.spotTime < t.perpTime ? t.spotTime : t.perpTime;
    require(_time != 0, ERR_INVALID_TIME);
    return _time;
}
```

Alternatively, expose both `spotTime` and `perpTime` individually so callers can assess each engine's freshness independently, mirroring the APRO team's own mitigation suggestion.

---

### Proof of Concept

1. Sequencer submits `SpotTick` with `time = T` → `spotTime = T`.
2. Sequencer stops submitting `PerpTick` for 24 hours → `perpTime = T - 86400`.
3. Any caller invokes `endpoint.getTime()`.
4. The function evaluates `T > (T - 86400)` → returns `T`.
5. The caller concludes the protocol is fully current, unaware that the perp engine's clock is 24 hours stale.

The perp engine's `updateStates(dt)` will receive a large `dt` spike when the next `PerpTick` eventually arrives, but `getTime()` will have reported freshness throughout the gap. [3](#0-2) [5](#0-4)

### Citations

**File:** core/contracts/EndpointStorage.sol (L41-46)
```text
    struct Times {
        uint128 perpTime;
        uint128 spotTime;
    }

    Times internal times;
```

**File:** core/contracts/EndpointTx.sol (L466-485)
```text
        } else if (txType == IEndpoint.TransactionType.SpotTick) {
            IEndpoint.SpotTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.SpotTick)
            );
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

**File:** core/contracts/Endpoint.sol (L344-349)
```text
    function getTime() external view returns (uint128) {
        Times memory t = times;
        uint128 _time = t.spotTime > t.perpTime ? t.spotTime : t.perpTime;
        require(_time != 0, ERR_INVALID_TIME);
        return _time;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L355-355)
```text
    function getTime() external view returns (uint128);
```
