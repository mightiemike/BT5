### Title
Unvalidated Sequencer Timestamp in `SpotTick`/`PerpTick` Enables Inflated Interest and Funding Rate Accrual via Slow Mode - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `SpotTick` and `PerpTick` transaction handlers in `EndpointTx.sol` compute `dt` directly from a caller-supplied `txn.time` without validating it against `block.timestamp`. Because any unprivileged user can enqueue a `SpotTick` or `PerpTick` via the slow mode queue (paying only the slow mode fee), and force-execute it after the 3-day delay, an attacker can inject an arbitrarily inflated `txn.time`. This causes `dt` to be inflated up to just under 7 days in a single tick, corrupting cumulative interest multipliers in `SpotEngineState` and cumulative funding accumulators in `PerpEngineState`, and permanently advancing `times.spotTime`/`times.perpTime` to a future value that breaks subsequent legitimate sequencer ticks.

---

### Finding Description

In `EndpointTx.sol`, the `SpotTick` and `PerpTick` handlers compute the time delta as:

```solidity
uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
spotEngine.updateStates(dt);
t.spotTime = txn.time;
``` [1](#0-0) 

```solidity
uint128 dt = t.perpTime == 0 ? 0 : txn.time - t.perpTime;
perpEngine.updateStates(dt, txn.avgPriceDiffs);
t.perpTime = txn.time;
``` [2](#0-1) 

`txn.time` is taken directly from the transaction payload with no validation against `block.timestamp`. The only guard is:

```solidity
require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
``` [3](#0-2) [4](#0-3) 

This allows `dt` up to just under 7 days.

The slow mode submission path in `EndpointTx.sol` does **not** restrict `SpotTick` or `PerpTick` transaction types — they fall into the `else` branch that only charges a fee:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [5](#0-4) 

The slow mode transaction is stored with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` and can be force-executed by anyone after the delay:

```solidity
require(
    fromSequencer || (txn.executableAt <= block.timestamp),
    ERR_SLOW_TX_TOO_RECENT
);
``` [6](#0-5) 

**Attack path:**
1. Attacker calls `submitSlowModeTransaction` with a `SpotTick` payload where `txn.time = current_spotTime + (7 * SECONDS_PER_DAY - 1)`.
2. After 3 days, attacker force-executes the slow mode transaction.
3. `dt ≈ 7 days` passes the guard and is fed into `SpotEngineState._updateState`:

```solidity
borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
``` [7](#0-6) 

4. `state.cumulativeBorrowsMultiplierX18` and `state.cumulativeDepositsMultiplierX18` are compounded by ~7 days of interest in a single tick, corrupting all normalized balances.
5. `t.spotTime` is permanently set to a future timestamp. The sequencer's next legitimate `SpotTick` (with a real `txn.time` < stored `t.spotTime`) produces an underflowed `dt` that exceeds the 7-day guard, causing all subsequent sequencer `SpotTick` calls to revert until real time catches up.

The same attack applies to `PerpTick`, inflating `cumulativeFundingLongX18`/`cumulativeFundingShortX18`:

```solidity
int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
state.cumulativeFundingLongX18 += paymentAmount;
state.cumulativeFundingShortX18 += paymentAmount;
``` [8](#0-7) 

---

### Impact Explanation

- **Spot engine**: `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` are inflated by up to 7 days of compounded interest in one tick. Every user's normalized balance is now worth a different real amount than intended. Borrowers are charged ~7 days of interest instantly; depositors receive ~7 days of yield instantly. This corrupts the solvency accounting of the entire spot engine.
- **Perp engine**: Funding accumulators are inflated by up to 7 days of funding in one tick, causing incorrect PnL settlement for all open perp positions and potentially triggering erroneous liquidations.
- **Oracle time corruption**: `getOracleTime()` returns the future timestamp, causing all orders with `expiration < future_time` to be treated as expired, silently rejecting valid user orders. [9](#0-8) [10](#0-9) 

---

### Likelihood Explanation

Any user holding enough quote tokens to pay the slow mode fee can execute this attack. The slow mode mechanism is a public, permissionless entry point. The attacker only needs to wait 3 days for the force-execution window to open. No privileged access, leaked keys, or social engineering is required.

---

### Recommendation

Validate `txn.time` against `block.timestamp` in both `SpotTick` and `PerpTick` handlers. Reject any tick whose submitted time exceeds `block.timestamp` by more than a small tolerance (e.g., a few minutes):

```solidity
require(txn.time <= block.timestamp + MAX_CLOCK_DRIFT, ERR_INVALID_TIME);
```

Additionally, restrict `SpotTick` and `PerpTick` transaction types from being submitted via the slow mode queue, since they are sequencer-internal operations with no legitimate user use case:

```solidity
require(
    txType != IEndpoint.TransactionType.SpotTick &&
    txType != IEndpoint.TransactionType.PerpTick,
    ERR_UNAUTHORIZED
);
```

---

### Proof of Concept

1. Observe current `t.spotTime = T` (e.g., `T = 1_000_000`).
2. Attacker calls `submitSlowModeTransaction` with a `SpotTick` payload: `txn.time = T + 6 * 86400 + 82800` (6 days 23 hours).
3. Slow mode fee is charged; transaction is queued with `executableAt = block.timestamp + 3 days`.
4. After 3 days, attacker calls the public slow mode execution function.
5. `dt = (T + 6*86400 + 82800) - T = 6*86400 + 82800 = 601200 seconds` — passes `dt < 7 * 86400 = 604800`.
6. `SpotEngineState._updateState` compounds interest for 601200 seconds (~6.96 days) in one call.
7. `t.spotTime` is set to `T + 601200`.
8. Sequencer's next `SpotTick` with real `txn.time = T + 3*86400` (3 days later) computes `dt = (T + 3*86400) - (T + 601200)` which underflows in `uint128`, producing a value >> `7 * SECONDS_PER_DAY`, causing the `require(dt < 7 * SECONDS_PER_DAY)` check to revert. The sequencer cannot update spot interest rates until `block.timestamp > T + 601200`.

### Citations

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L471-475)
```text
            Times memory t = times;
            uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
            spotEngine.updateStates(dt);
            t.spotTime = txn.time;
            times = t;
```

**File:** core/contracts/EndpointTx.sol (L481-485)
```text
            Times memory t = times;
            uint128 dt = t.perpTime == 0 ? 0 : txn.time - t.perpTime;
            perpEngine.updateStates(dt, txn.avgPriceDiffs);
            t.perpTime = txn.time;
            times = t;
```

**File:** core/contracts/PerpEngineState.sol (L114-114)
```text
            require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
```

**File:** core/contracts/PerpEngineState.sol (L130-132)
```text
                int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
                state.cumulativeFundingLongX18 += paymentAmount;
                state.cumulativeFundingShortX18 += paymentAmount;
```

**File:** core/contracts/SpotEngineState.sol (L98-98)
```text
            borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
```

**File:** core/contracts/SpotEngineState.sol (L267-267)
```text
        require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
```

**File:** core/contracts/Endpoint.sol (L196-199)
```text
        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );
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
