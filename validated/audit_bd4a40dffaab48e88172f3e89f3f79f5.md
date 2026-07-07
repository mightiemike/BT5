### Title
`Endpoint.getTime()` Returns `MAX(spotTime, perpTime)`, Enabling Premature NLP Balance Unlock — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.getTime()` aggregates two independent oracle timestamps (`spotTime`, `perpTime`) and returns their **maximum** rather than their minimum. This directly mirrors the `ProductApi3ReaderProxyV1` bug class: a composite timestamp masks the staleness of the lagging feed. Any caller can invoke `SpotEngineState.tryUnlockNlpBalance()` — a `public` function — and unlock NLP-locked collateral before the intended lock period has elapsed, as long as one feed is ahead of the other.

---

### Finding Description

`Endpoint.getTime()` is defined as:

```solidity
function getTime() external view returns (uint128) {
    Times memory t = times;
    uint128 _time = t.spotTime > t.perpTime ? t.spotTime : t.perpTime;
    require(_time != 0, ERR_INVALID_TIME);
    return _time;
}
``` [1](#0-0) 

`spotTime` and `perpTime` are updated independently by the sequencer via `SpotTick` and `PerpTick` transactions respectively:

```solidity
t.spotTime = txn.time;   // SpotTick handler
t.perpTime = txn.time;   // PerpTick handler
``` [2](#0-1) 

`getOracleTime()` in `EndpointGated` simply delegates to `getTime()`:

```solidity
function getOracleTime() internal view returns (uint128) {
    return IEndpoint(endpoint).getTime();
}
``` [3](#0-2) 

`tryUnlockNlpBalance` is `public` and uses `getOracleTime()` as the unlock gate:

```solidity
while (
    queue.unlockedUpTo < queue.balanceCount &&
    queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
)
``` [4](#0-3) 

Because `getTime()` returns `MAX(spotTime, perpTime)`, whenever the two feeds diverge — e.g., `spotTime = T+300` while `perpTime = T` — any NLP balance whose `unlockedAt` falls in the window `(T, T+300]` is treated as unlockable even though the lagging feed has not yet reached that timestamp. The intended lock period is bypassed.

---

### Impact Explanation

A user whose NLP balance has `unlockedAt = T+150` can call `tryUnlockNlpBalance` and receive their collateral 150 seconds early (relative to the lagging feed). In a scenario where the sequencer's perp-tick cadence lags spot-tick cadence by several minutes — a realistic operational condition — the exploitable window grows proportionally. The unlocked balance is immediately credited to `unlockedBalanceSum`, making it available for withdrawal or use as collateral, bypassing the protocol's intended lock invariant.

---

### Likelihood Explanation

`spotTime` and `perpTime` are updated by separate transaction types (`SpotTick` vs `PerpTick`). Any transient difference in sequencer submission cadence, gas conditions, or batch ordering creates a divergence. This is a normal operational condition, not an exceptional one. The entry point (`tryUnlockNlpBalance`) is `public` and requires no special role or permission — any user can call it for their own subaccount at any time.

---

### Recommendation

Change `getTime()` to return `MIN(spotTime, perpTime)` instead of `MAX`:

```solidity
function getTime() external view returns (uint128) {
    Times memory t = times;
    require(t.spotTime != 0 && t.perpTime != 0, ERR_INVALID_TIME);
    return t.spotTime < t.perpTime ? t.spotTime : t.perpTime;
}
```

This ensures the reported oracle time never exceeds the most-lagging feed, preserving the lock-period invariant regardless of tick-submission divergence — directly analogous to the `MIN(timestamp1, timestamp2)` recommendation in the source report.

---

### Proof of Concept

1. Sequencer submits `SpotTick` with `time = T+300`, advancing `spotTime` to `T+300`.
2. Sequencer has not yet submitted `PerpTick`; `perpTime` remains at `T`.
3. `getTime()` returns `MAX(T+300, T) = T+300`.
4. User's NLP balance has `unlockedAt = T+150`.
5. User calls `tryUnlockNlpBalance(subaccount)`.
6. Condition `T+150 <= T+300` is satisfied; balance is unlocked and credited to `unlockedBalanceSum`.
7. User withdraws collateral 150 seconds before the intended unlock time, bypassing the lock-period invariant. [5](#0-4) [1](#0-0)

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

**File:** core/contracts/EndpointGated.sol (L21-23)
```text
    function getOracleTime() internal view returns (uint128) {
        return IEndpoint(endpoint).getTime();
    }
```

**File:** core/contracts/SpotEngineState.sol (L285-306)
```text
    function tryUnlockNlpBalance(bytes32 subaccount)
        public
        returns (Balance memory)
    {
        NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
            subaccount
        ];
        while (
            queue.unlockedUpTo < queue.balanceCount &&
            queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
        ) {
            // we can unlock this balance
            queue.unlockedBalanceSum.amount += queue
                .balances[queue.unlockedUpTo]
                .balance
                .amount;
            delete queue.balances[queue.unlockedUpTo];
            queue.unlockedUpTo++;
        }

        return queue.unlockedBalanceSum;
    }
```
