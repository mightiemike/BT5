### Title
Slow Mode `WithdrawCollateral` Bypasses Product-Specific Withdrawal Fee — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

Nado has two execution paths for `WithdrawCollateral`: the fast path via the sequencer (`processTransactionImpl`) and the slow mode path (`processSlowModeTransactionImpl`). The fast path charges the product-specific `withdrawFeeX18` before executing the withdrawal. The slow mode path omits this fee charge entirely, allowing any user to withdraw collateral fee-free by routing through slow mode.

---

### Finding Description

**Fast path** (`processTransactionImpl`, lines 413–436) enforces the full validation and fee pipeline:

```
validateSignedTx(...)          // nonce + signature + subaccount check
chargeFee(sender, withdrawFeeX18, productId)   // product-specific fee
clearinghouse.withdrawCollateral(...)
``` [1](#0-0) 

**Slow mode path** (`processSlowModeTransactionImpl`, lines 217–229) skips the fee entirely:

```
validateSender(txn.sender, sender)   // only checks msg.sender == address in subaccount
clearinghouse.withdrawCollateral(...)  // no fee charged
``` [2](#0-1) 

The slow mode submission step (`submitSlowModeTransactionImpl`) only charges a flat `SLOW_MODE_FEE` upfront — it does not substitute for the per-product `withdrawFeeX18`. [3](#0-2) 

The `WithdrawCollateralV2` fast path also enforces the fee, and even validates that the fee submitted by the sequencer does not exceed the configured maximum: [4](#0-3) 

There is no `WithdrawCollateralV2` handler in `processSlowModeTransactionImpl` at all, so the only slow mode withdrawal path is the fee-free `WithdrawCollateral` branch.

---

### Impact Explanation

Any user can avoid paying the product-specific withdrawal fee (`withdrawFeeX18`) by submitting a `WithdrawCollateral` transaction through the slow mode path instead of the fast path. The protocol loses fee revenue on every such withdrawal. The broken invariant is: *all collateral withdrawals must pay the configured `withdrawFeeX18` for the withdrawn product*. The slow mode path violates this invariant unconditionally.

---

### Likelihood Explanation

High. The slow mode path is a permissionless, publicly callable entry point — any user with a registered subaccount can call `submitSlowModeTransaction` with a `WithdrawCollateral` payload. The only cost is the flat `SLOW_MODE_FEE` and a 3-day delay. For large withdrawals where `withdrawFeeX18` exceeds `SLOW_MODE_FEE`, the slow mode path is strictly cheaper, giving every large withdrawer a direct financial incentive to exploit this path.

---

### Recommendation

In `processSlowModeTransactionImpl`, add the product-specific fee charge for `WithdrawCollateral`, mirroring the fast path:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
+   chargeFee(
+       txn.sender,
+       spotEngine.getConfig(txn.productId).withdrawFeeX18,
+       txn.productId
+   );
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        address(0),
        nSubmissions
    );
```

---

### Proof of Concept

1. User holds 10,000 USDC in subaccount `0xAlice...default`. The configured `withdrawFeeX18` for the USDC product is 0.1% (10 USDC on this withdrawal).
2. User calls `Endpoint.submitSlowModeTransaction(withdrawCollateralPayload)`, paying only the flat `SLOW_MODE_FEE` (e.g., 1 USDC).
3. After 3 days, anyone calls `Endpoint.executeSlowModeTransaction()` (or the sequencer includes `ExecuteSlowMode`).
4. `_executeSlowModeTransaction` → `processSlowModeTransaction` → `processSlowModeTransactionImpl` handles the `WithdrawCollateral` branch.
5. `validateSender` passes (msg.sender matches the address embedded in `txn.sender`).
6. `clearinghouse.withdrawCollateral` executes — no `chargeFee` call occurs.
7. User receives the full 10,000 USDC, having paid only 1 USDC (flat fee) instead of 10 USDC (product fee). The 9 USDC difference is protocol fee revenue that is permanently lost. [5](#0-4) [2](#0-1)

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

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
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
