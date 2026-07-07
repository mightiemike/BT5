### Title
`WithdrawCollateralV2` Slow-Mode Transactions Are Silently Discarded After Fee Collection — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`WithdrawCollateralV2` is a fully supported transaction type in the sequencer path and the fast-withdrawal path, but it is absent from `processSlowModeTransactionImpl`. Any user who submits a `WithdrawCollateralV2` via `submitSlowModeTransaction` pays the slow-mode fee, has the transaction queued, and then watches it silently fail when executed — with no refund and no withdrawal.

---

### Finding Description

`EndpointTx.submitSlowModeTransactionImpl` accepts any transaction type that is not explicitly blocked or owner-gated, charging a slow-mode fee and queuing it: [1](#0-0) 

`WithdrawCollateralV2` is not in the owner-only list and is not explicitly reverted, so it falls through to the `else` branch, pays the fee, and is queued.

`processSlowModeTransactionImpl` handles `WithdrawCollateral` (V1): [2](#0-1) 

But it has **no branch for `WithdrawCollateralV2`**. The function ends with a bare `revert()`: [3](#0-2) 

`_executeSlowModeTransaction` catches this revert silently and does not refund the user: [4](#0-3) 

The comment `// try return funds now removed` confirms the refund path was deliberately deleted, leaving the user with no recourse.

By contrast, `processTransactionImpl` (the sequencer path) fully handles `WithdrawCollateralV2`: [5](#0-4) 

`BaseWithdrawPool.resolveFastWithdrawal` also handles `WithdrawCollateralV2` for the fast-withdrawal path: [6](#0-5) 

`WithdrawCollateralV2` is therefore a first-class transaction type everywhere except the slow-mode processing path.

---

### Impact Explanation

The slow-mode path is the protocol's censorship-resistance mechanism — it is the only way a user can force a withdrawal if the sequencer is unresponsive or censoring them. `WithdrawCollateralV2` adds the critical `sendTo` field, allowing users to withdraw to an address other than the subaccount owner (e.g., a cold wallet). Users who submit `WithdrawCollateralV2` via slow mode:

1. Lose the slow-mode fee (deducted from their quote balance).
2. Have their withdrawal silently dropped — collateral remains locked in the clearinghouse.
3. Cannot use the censorship-resistance path for V2 withdrawals at all.

The only workaround is to fall back to `WithdrawCollateral` V1, which forces the withdrawal to the subaccount owner's address and removes the `sendTo` flexibility that V2 was designed to provide.

---

### Likelihood Explanation

Any unprivileged user who calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateralV2` payload triggers this path. No special role, sequencer access, or governance action is required. The scenario is realistic whenever a user wants to withdraw to a custom address via the censorship-resistance path, or when the sequencer is unavailable and the user falls back to slow mode.

---

### Recommendation

Add a `WithdrawCollateralV2` branch to `processSlowModeTransactionImpl` in `core/contracts/EndpointTx.sol`, mirroring the existing `WithdrawCollateral` branch but decoding `SignedWithdrawCollateralV2` and passing `signedTx.tx.sendTo` to `clearinghouse.withdrawCollateral`. Alternatively, explicitly revert in `submitSlowModeTransactionImpl` for `WithdrawCollateralV2` (as is done for `DepositCollateral`) so users are not charged a fee for an unsupported operation.

---

### Proof of Concept

1. Alice holds collateral in the clearinghouse and wants to withdraw to a cold wallet address via slow mode (sequencer is censoring her).
2. Alice calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateralV2` payload specifying `sendTo = coldWallet`.
3. `submitSlowModeTransactionImpl` charges the slow-mode fee from Alice's quote balance and queues the transaction.
4. After the timeout, Alice (or anyone) calls `Endpoint.executeSlowModeTransaction`.
5. `processSlowModeTransactionImpl` reaches the final `revert()` because no branch handles `WithdrawCollateralV2`.
6. `_executeSlowModeTransaction` catches the revert silently; no refund is issued.
7. Alice has lost her slow-mode fee and her collateral remains locked. She cannot withdraw to her cold wallet via the censorship-resistance path.

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

**File:** core/contracts/EndpointTx.sol (L327-330)
```text
        } else {
            revert();
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L355-372)
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

**File:** core/contracts/Endpoint.sol (L205-228)
```text
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
```

**File:** core/contracts/BaseWithdrawPool.sol (L67-78)
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
        revert("Invalid withdrawal tx type");
```
