### Title
`WithdrawCollateralV2` Slow-Mode Submission Silently Fails After Charging Fee — (`core/contracts/EndpointTx.sol`)

---

### Summary

`submitSlowModeTransactionImpl` accepts `WithdrawCollateralV2` transactions — charging a slow-mode fee and queuing them — but `processSlowModeTransactionImpl` has no handler for this transaction type and unconditionally reverts. On mainnet the revert is silently swallowed by the try/catch in `_executeSlowModeTransaction`. The result is a permanent loss of the slow-mode fee and a withdrawal that is accepted but never executed.

---

### Finding Description

**Two parallel code paths handle withdrawals differently:**

**Fast path (sequencer)** — `processTransactionImpl` in `EndpointTx.sol` handles both `WithdrawCollateral` and `WithdrawCollateralV2`: [1](#0-0) 

**Slow-mode submission path** — `submitSlowModeTransactionImpl` does not explicitly reject `WithdrawCollateralV2`. It is not `DepositCollateral` (which reverts), not `DepositInsurance`, and not in the owner-only list, so it falls into the `else` branch: [2](#0-1) 

The slow-mode fee is charged and the transaction is queued. No error is returned to the caller.

**Slow-mode execution path** — `processSlowModeTransactionImpl` handles `WithdrawCollateral` (V1) but has **no case for `WithdrawCollateralV2`**. It falls through to the terminal `revert()`: [3](#0-2) [4](#0-3) 

**The revert is silently swallowed on mainnet.** `_executeSlowModeTransaction` wraps the call in a try/catch. The comment `// try return funds now removed` confirms that a prior refund mechanism was deleted: [5](#0-4) 

The slow-mode fee is permanently lost, the queued entry is consumed, and the withdrawal never executes.

---

### Impact Explanation

1. **Slow-mode fee permanently lost** — the quote token fee is deducted from the user's balance and credited to `slowModeFees` with no refund path.
2. **Withdrawal never executes** — the user's collateral balance is never debited and no tokens are transferred out, but the user's intent (withdraw to a custom `sendTo` address) is silently discarded.
3. **False success** — `submitSlowModeTransaction` returns without error; the user observes a queued transaction and reasonably expects it to execute after the delay.
4. **Inconsistency between paths** — `WithdrawCollateralV2` works correctly via the sequencer fast path but silently fails via the slow-mode path, creating the same desynchronization class as M-7.

---

### Likelihood Explanation

`WithdrawCollateralV2` is the only withdrawal variant that supports a custom `sendTo` address: [6](#0-5) 

A user who needs to withdraw to an address other than their own subaccount address, and who chooses the slow-mode path (e.g., because the sequencer is unavailable or they distrust it), will trigger this bug. The slow-mode path is a documented, user-accessible entry point (`submitSlowModeTransaction` is `external`), making this reachable without any privileged access.

---

### Recommendation

Explicitly reject `WithdrawCollateralV2` in `submitSlowModeTransactionImpl`, mirroring how `DepositCollateral` is rejected:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
    revert(); // V2 not supported via slow mode
```

Alternatively, add a `WithdrawCollateralV2` handler in `processSlowModeTransactionImpl` that mirrors the `WithdrawCollateral` handler but also respects the `sendTo` field.

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateralV2` payload (type byte `0x??`, `sendTo != address(0)`).
2. `submitSlowModeTransactionImpl` hits the `else` branch: slow-mode fee is charged, tx is pushed to `slowModeTxs`. [2](#0-1) 
3. After `SLOW_MODE_TX_DELAY` (3 days), anyone calls `executeSlowModeTransaction` or the sequencer submits `ExecuteSlowMode`.
4. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(txn.sender, txn.tx)` inside a try/catch. [7](#0-6) 
5. `processSlowModeTransactionImpl` reaches `else { revert(); }` — no `WithdrawCollateralV2` case exists. [4](#0-3) 
6. The revert is caught silently. The slow-mode fee is gone. The withdrawal never happens. The user has no on-chain indication of failure.

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
