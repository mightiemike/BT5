### Title
`WithdrawCollateralV2` Slow-Mode Handler Never Implemented in `processSlowModeTransactionImpl` — User Fee Burned and Withdrawal Silently Fails — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

`WithdrawCollateralV2` is a fully supported transaction type in the sequencer fast path (`processTransactionImpl`) and in the fast-withdrawal pool (`BaseWithdrawPool.resolveFastWithdrawal`). However, the slow-mode execution path (`processSlowModeTransactionImpl`) has no handler for it. Simultaneously, `submitSlowModeTransactionImpl` does not gate `WithdrawCollateralV2` to owner-only, so any user can queue it as a slow-mode transaction and pay the slow-mode fee. When the transaction is later executed, `processSlowModeTransactionImpl` falls through to the unconditional `revert()`, burning the user's fee and permanently failing the withdrawal.

---

### Finding Description

**Step 1 — Submission is accepted and fee is charged.**

`submitSlowModeTransactionImpl` has an explicit owner-only allowlist: [1](#0-0) 

`WithdrawCollateralV2` is absent from that list, so execution falls to the `else` branch: [2](#0-1) 

The slow-mode fee is charged and the transaction is enqueued without error.

**Step 2 — Execution always reverts.**

`processSlowModeTransactionImpl` handles `WithdrawCollateral` (the V1 variant) but contains no branch for `WithdrawCollateralV2`. The function terminates with an unconditional revert: [3](#0-2) 

**Step 3 — The fast path proves the type is valid.**

`processTransactionImpl` fully handles `WithdrawCollateralV2`, including signature validation, fee charging, and collateral withdrawal: [4](#0-3) 

`BaseWithdrawPool.resolveFastWithdrawal` also explicitly decodes `WithdrawCollateralV2`: [5](#0-4) 

The type is therefore a first-class protocol transaction type — its slow-mode handler was simply never written.

**Root cause (analog to the reference bug):** The `WithdrawCollateralV2` branch inside `processSlowModeTransactionImpl` is the "authorized function that is never used." It was supposed to be invoked when the sequencer (or the user after the timeout) executes the queued slow-mode transaction, but it was never implemented. This is structurally identical to `resetTokenStateByArbManager()` existing in the interface but never being called: the intended execution path is absent, leaving the associated state (the queued withdrawal) permanently unresolvable.

---

### Impact Explanation

- The user's slow-mode fee (`SLOW_MODE_FEE`) is permanently consumed — it is charged during submission and never refunded.
- The withdrawal itself never executes; the user's collateral remains locked in the protocol.
- The queued `SlowModeTx` slot is consumed and deleted on the failed execution attempt, so there is no retry mechanism.

---

### Likelihood Explanation

`WithdrawCollateralV2` is the current canonical withdrawal transaction type (it adds a `sendTo` field absent in V1). Any user who attempts a slow-mode `WithdrawCollateralV2` — for example, after the sequencer is unresponsive and the three-day timeout elapses — will trigger this path. The entry point (`submitSlowModeTransaction`) is public and requires no privilege. [6](#0-5) 

---

### Recommendation

Add a `WithdrawCollateralV2` branch to `processSlowModeTransactionImpl` that mirrors the existing `WithdrawCollateral` branch, decoding `SignedWithdrawCollateralV2`, validating the sender, and calling `clearinghouse.withdrawCollateral` with the resolved `sendTo` address. Alternatively, if slow-mode `WithdrawCollateralV2` is intentionally unsupported, add an explicit `revert` in `submitSlowModeTransactionImpl` for that type so the fee is never charged.

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction(txBytes)` where `txBytes[0] == TransactionType.WithdrawCollateralV2`.
2. `submitSlowModeTransactionImpl` does not match any owner-only branch → charges `SLOW_MODE_FEE` from the user, enqueues the transaction. [7](#0-6) 
3. Three days later, user (or anyone) calls `Endpoint.executeSlowModeTransaction()`.
4. `_executeSlowModeTransaction` deletes the slot and calls `this.processSlowModeTransaction`. [8](#0-7) 
5. `processSlowModeTransactionImpl` finds no matching branch for `WithdrawCollateralV2` and hits `revert()`. [3](#0-2) 
6. The outer `try/catch` in `_executeSlowModeTransaction` silently swallows the revert (on non-hardhat chains). The slot is already deleted. The user's fee is gone and the withdrawal is permanently lost.

### Citations

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

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```

**File:** core/contracts/Endpoint.sol (L193-228)
```text
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
```
