### Title
Unsupported `WithdrawCollateralV2` Transaction Type Silently Accepted and Fee-Charged in Slow Mode Queue — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

`submitSlowModeTransactionImpl` accepts and charges the slow-mode fee for `WithdrawCollateralV2` transactions because the type falls into the catch-all `else` branch. However, `processSlowModeTransactionImpl` has no handler for `WithdrawCollateralV2` and hits `else { revert(); }`. The revert is silently swallowed by the `try/catch` in `_executeSlowModeTransaction`. The user loses their slow-mode fee, waits the full 3-day delay, and their withdrawal never executes.

---

### Finding Description

`submitSlowModeTransactionImpl` explicitly lists the transaction types that require `owner()` authorization. Every other type — including `WithdrawCollateralV2` — falls into the `else` branch, which charges the slow-mode fee and enqueues the transaction: [1](#0-0) 

`processSlowModeTransactionImpl` handles `WithdrawCollateral` (V1) but has no corresponding branch for `WithdrawCollateralV2`. Any unrecognized type hits the terminal `revert()`: [2](#0-1) [3](#0-2) 

When the queued transaction is executed, `_executeSlowModeTransaction` wraps the call in a `try/catch`. The revert from the unhandled type is caught and discarded. The comment `// try return funds now removed` confirms that fee refund logic was deliberately removed, so the user has no recourse: [4](#0-3) 

`WithdrawCollateralV2` is a live, sequencer-path transaction type with its own handler in `processTransactionImpl` and in `BaseWithdrawPool.resolveFastWithdrawal`, confirming it is a user-facing feature: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A user invoking the censorship-resistance slow-mode path with a `WithdrawCollateralV2` transaction (e.g., to send collateral to a `sendTo` address different from the subaccount owner) will:

1. Pay the slow-mode fee (real token transfer from the user's wallet).
2. Wait the full `SLOW_MODE_TX_DELAY` (3 days).
3. Have the transaction silently fail — collateral is never released.
4. Receive no on-chain error or event indicating failure.

The user loses the slow-mode fee with no refund path and their withdrawal is permanently blocked until they discover the issue and resubmit using `WithdrawCollateral` V1, which does not support the `sendTo` field. This is a concrete asset loss (slow-mode fee) combined with a disruption of the expected withdrawal flow — the exact analog of the external report's "improper input handling" class where an accepted input causes an unexpected silent failure state.

---

### Likelihood Explanation

The slow-mode queue is the protocol's censorship-resistance mechanism. A user whose sequencer-path `WithdrawCollateralV2` is being censored would naturally attempt to submit the same transaction type via slow mode. The `WithdrawCollateralV2` type is documented and live in the sequencer path, so users have no reason to suspect it is unsupported in slow mode. The entry path requires no special privilege — any wallet can call `submitSlowModeTransaction`.

---

### Recommendation

Add a `WithdrawCollateralV2` handler in `processSlowModeTransactionImpl` that mirrors the V1 handler but decodes `SignedWithdrawCollateralV2` and passes `signedTx.tx.sendTo` to `clearinghouse.withdrawCollateral`. Alternatively, add an explicit upfront revert in `submitSlowModeTransactionImpl` for transaction types that are not supported in the slow-mode execution path, so users receive an immediate error rather than a silent failure after paying the fee and waiting 3 days.

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateralV2` transaction body.
2. `submitSlowModeTransactionImpl` is reached via `delegatecall`. The type is not in the owner-only list, so the `else` branch executes: `chargeSlowModeFee` transfers tokens from the user, and the transaction is pushed to `slowModeTxs`. [7](#0-6) 
3. After `SLOW_MODE_TX_DELAY`, the sequencer or user calls `executeSlowModeTransaction`. `_executeSlowModeTransaction` dequeues the transaction and calls `this.processSlowModeTransaction` inside a `try/catch`. [8](#0-7) 
4. `processSlowModeTransactionImpl` decodes `txType = WithdrawCollateralV2`. No matching branch exists; execution reaches `else { revert(); }`. [3](#0-2) 
5. The revert is caught by the `try/catch`. Gas heuristic passes (normal revert, not OOG). Execution continues silently. The user's collateral is never transferred. The slow-mode fee is not refunded. [9](#0-8)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L327-329)
```text
        } else {
            revert();
        }
```

**File:** core/contracts/EndpointTx.sol (L355-384)
```text
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
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

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L185-229)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L67-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
```
