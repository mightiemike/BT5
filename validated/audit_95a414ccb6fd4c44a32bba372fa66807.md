### Title
Slow Mode `WithdrawCollateral` Bypasses `withdrawFeeX18` Fee Collection — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `processSlowModeTransactionImpl` function in `EndpointTx.sol` handles `WithdrawCollateral` transactions without charging the `withdrawFeeX18` fee that the normal sequencer path enforces in `processTransactionImpl`. Any unprivileged user can exploit this by routing withdrawals through the slow mode path, paying only the flat `SLOW_MODE_FEE` and avoiding the per-product withdrawal fee entirely.

---

### Finding Description

In the normal sequencer path, `processTransactionImpl` handles `WithdrawCollateral` by first charging the product-specific withdrawal fee before executing the withdrawal: [1](#0-0) 

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
clearinghouse.withdrawCollateral(...);
```

In the slow mode path, `processSlowModeTransactionImpl` handles the same `WithdrawCollateral` transaction type with only a sender address check — no `withdrawFeeX18` is charged: [2](#0-1) 

```solidity
validateSender(txn.sender, sender);
clearinghouse.withdrawCollateral(
    txn.sender,
    txn.productId,
    txn.amount,
    address(0),
    nSubmissions
);
```

The `submitSlowModeTransactionImpl` function does not block `WithdrawCollateral` — it falls into the `else` branch that charges only the flat `SLOW_MODE_FEE`: [3](#0-2) 

The `chargeFee` function deducts the fee from the user's balance and credits `sequencerFee[productId]`, which is protocol revenue: [4](#0-3) 

The slow mode path skips this deduction entirely, meaning `sequencerFee[productId]` is never incremented for slow mode withdrawals.

---

### Impact Explanation

Any user can withdraw collateral without paying `withdrawFeeX18` by using the slow mode path. The `withdrawFeeX18` is a per-product fee configured in `SpotEngine` and credited to `sequencerFee[productId]`, which is the protocol's sequencer revenue. Systematic use of slow mode for all withdrawals drains this revenue stream. The broken invariant is: *all `WithdrawCollateral` executions must charge `withdrawFeeX18`*, which the slow mode path violates.

---

### Likelihood Explanation

High. The slow mode path is a supported, permissionless entrypoint callable by any user at any time. The only cost is the flat `SLOW_MODE_FEE` and a 3-day wait. Whenever `withdrawFeeX18 > SLOW_MODE_FEE` (in value), users are economically incentivized to always use slow mode for withdrawals. No special privileges, compromised keys, or governance capture are required.

---

### Recommendation

Apply the same `withdrawFeeX18` fee charge in `processSlowModeTransactionImpl` for `WithdrawCollateral` transactions, mirroring the logic in `processTransactionImpl`:

```solidity
// In processSlowModeTransactionImpl, WithdrawCollateral branch:
chargeFee(
    txn.sender,
    spotEngine.getConfig(txn.productId).withdrawFeeX18,
    txn.productId
);
clearinghouse.withdrawCollateral(...);
```

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral` payload targeting their own subaccount.
2. `submitSlowModeTransactionImpl` charges only the flat `SLOW_MODE_FEE` and enqueues the transaction. [5](#0-4) 
3. After 3 days, anyone calls `Endpoint.executeSlowModeTransaction()`. [6](#0-5) 
4. `processSlowModeTransactionImpl` executes the withdrawal via `clearinghouse.withdrawCollateral` with no `withdrawFeeX18` deduction. [2](#0-1) 
5. The user receives their full collateral. `sequencerFee[productId]` is never incremented. The fee that would have been charged in the normal path is silently skipped.

### Citations

**File:** core/contracts/EndpointTx.sol (L134-141)
```text
    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L369-384)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

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

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
