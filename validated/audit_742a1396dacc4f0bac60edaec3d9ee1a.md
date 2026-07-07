### Title
Slow Mode Transaction Fee Permanently Lost When Execution Fails Silently — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

Users who submit slow mode transactions (e.g., `WithdrawCollateral`) pay a non-refundable `SLOW_MODE_FEE` ($1 USDC) at submission time. If the queued transaction fails during execution — which is silently swallowed via a bare `try/catch` — the fee is permanently lost with no refund path and no retry mechanism. The 3-day delay between submission and execution makes state-change-induced failures a realistic and recurring scenario.

---

### Finding Description

The slow mode system in Nado operates in two distinct phases separated by up to 3 days:

**Phase 1 — Submission (`submitSlowModeTransactionImpl`):**

A user calls `submitSlowModeTransaction` with a transaction such as `WithdrawCollateral`. For all transaction types not in the privileged admin list, `chargeSlowModeFee` is called immediately, pulling `SLOW_MODE_FEE` ($1 USDC) from the user's wallet: [1](#0-0) 

The transaction is then enqueued with a 3-day execution delay: [2](#0-1) 

**Phase 2 — Execution (`_executeSlowModeTransaction`):**

When the sequencer (or anyone after the delay) executes the queued transaction, failures are silently swallowed via a bare `try/catch` with no error handling, no refund, and no retry: [3](#0-2) 

The transaction is deleted from the queue regardless of success or failure: [4](#0-3) 

**Concrete failure trigger for `WithdrawCollateral`:**

A user submits a `WithdrawCollateral` slow mode transaction when their account health is healthy. Over the 3-day window, price movements cause their health to deteriorate. When the sequencer executes the transaction, `clearinghouse.withdrawCollateral` calls `require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH)`: [5](#0-4) 

This `require` reverts, the `try/catch` silently discards the failure, the transaction is deleted, and the user's $1 fee is gone with no recourse.

The `SLOW_MODE_FEE` constant confirms the fee value: [6](#0-5) 

The 3-day delay is hardcoded: [7](#0-6) 

---

### Impact Explanation

**Impact: Medium**

Users permanently lose the $1 USDC slow mode fee when their queued transaction fails during execution. There is no on-chain refund path and no retry mechanism — the transaction slot is deleted unconditionally. While $1 per transaction is modest, the loss is guaranteed and unrecoverable for any user whose account state changes adversely during the 3-day window. Users have no way to cancel a queued slow mode transaction before execution either, so they cannot mitigate the loss once submitted.

---

### Likelihood Explanation

**Likelihood: Medium**

The 3-day mandatory delay (`SLOW_MODE_TX_DELAY`) is the primary driver. In a perpetual futures DEX with leveraged positions, account health can change materially within 3 days due to:
- Price movements affecting collateral value
- Funding rate accrual
- Partial liquidations reducing available balance
- Other concurrent withdrawals processed by the sequencer before the slow mode tx

Any user submitting a `WithdrawCollateral` slow mode transaction near their health boundary is at realistic risk of hitting this path.

---

### Recommendation

1. **Refund the slow mode fee on execution failure.** In the `catch` block of `_executeSlowModeTransaction`, refund `SLOW_MODE_FEE` to `txn.sender` instead of silently discarding it.
2. **Allow cancellation of queued slow mode transactions.** Provide a user-callable function to cancel their own pending slow mode transaction and recover the fee before execution.
3. **Alternatively, charge the fee at execution time** rather than at submission time, so users only pay when the transaction actually succeeds.

---

### Proof of Concept

1. Alice has a healthy account and submits a `WithdrawCollateral` slow mode transaction via `submitSlowModeTransaction`. She pays $1 USDC via `chargeSlowModeFee` at line 370 of `EndpointTx.sol`. The transaction is queued with `executableAt = block.timestamp + 3 days`.
2. Over the next 3 days, market prices move against Alice's open perp positions. Her account health drops below zero.
3. The sequencer calls `executeSlowModeTransaction`. `_executeSlowModeTransaction` dequeues Alice's transaction and calls `this.processSlowModeTransaction` inside a `try` block.
4. Inside `processSlowModeTransactionImpl`, `clearinghouse.withdrawCollateral` is called, which reaches `require(getHealth(sender, IProductEngine.HealthType.INITIAL) >= 0, ERR_SUBACCT_HEALTH)` and reverts.
5. The `catch {}` block executes with no body — no refund, no event, no retry.
6. Alice's $1 USDC fee is permanently lost. Her withdrawal never executed. She has no on-chain mechanism to recover the fee or resubmit.

### Citations

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L374-384)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
```

**File:** core/contracts/Endpoint.sol (L205-210)
```text
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
