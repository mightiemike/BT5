### Title
Silent Failure of Slow Mode Transactions Causes Permanent Loss of User Deposit Funds — (File: `core/contracts/Endpoint.sol`)

---

### Summary

When a slow mode transaction fails during execution, the transaction is silently dropped and the user's deposited tokens are permanently locked in the contract. This is a direct analog to the GMX M-30 vulnerability: a protocol-level state change (product delisting) that occurs after a user's operation is queued causes that operation to fail silently on execution, with no fund recovery path.

---

### Finding Description

In `Endpoint._executeSlowModeTransaction`, the slow mode transaction entry is **deleted from the queue before execution** at line 194:

```solidity
delete slowModeTxs[_slowModeConfig.txUpTo++];
```

The subsequent call to `processSlowModeTransaction` is wrapped in a try/catch:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
```

The catch block does **nothing** to return funds. The comment `// try return funds now removed` at line 226 explicitly acknowledges that a fund-recovery path previously existed and was deliberately removed.

The vulnerable deposit flow is in `depositCollateralWithReferral` (lines 123–167 of `Endpoint.sol`):

1. The user's ERC-20 tokens are transferred into the contract via `handleDepositTransfer` (line 144).
2. A `DepositCollateral` slow mode transaction is queued with a 3-day delay (line 152).

If `clearinghouse.depositCollateral` reverts during execution of that slow mode transaction (e.g., because the product was delisted between deposit and execution), the catch block fires, the transaction slot is already deleted, and the user's tokens remain in the contract with no recovery mechanism.

The same pattern applies to `WithdrawCollateral` slow mode transactions submitted via `submitSlowModeTransaction`: the slow mode fee is charged at submission time (`chargeSlowModeFee` at line 370 of `EndpointTx.sol`), and if execution fails, the fee is permanently lost.

---

### Impact Explanation

- **For `DepositCollateral` slow mode transactions**: The user's full deposit amount (ERC-20 tokens) is transferred to the contract at submission time. If the slow mode transaction fails during execution, those tokens are permanently locked in the contract with no recovery path. This is a direct loss of principal.
- **For `WithdrawCollateral` slow mode transactions**: The slow mode fee paid at submission is permanently lost if execution fails.
- The 3-day mandatory delay (`SLOW_MODE_TX_DELAY`) between submission and execution creates a wide window for protocol state changes (e.g., product delisting) to invalidate queued transactions.

---

### Likelihood Explanation

- A user deposits collateral for product X via `depositCollateralWithReferral`. This is a normal, unprivileged user action.
- The protocol subsequently delists product X via a `DelistProduct` slow mode transaction (owner-only, but a routine maintenance operation).
- After the 3-day delay, the sequencer or any caller triggers `executeSlowModeTransaction` for the user's `DepositCollateral` transaction.
- `clearinghouse.depositCollateral` reverts because the product no longer exists.
- The catch block fires silently; the user's tokens are gone.

The 3-day delay is the key amplifier: it is long enough that product lifecycle changes (delistings, migrations) are realistic between deposit submission and execution. This is not a theoretical edge case — it is a predictable operational scenario.

---

### Recommendation

Restore the fund-recovery logic in the catch block of `_executeSlowModeTransaction`. Specifically:

1. For `DepositCollateral` slow mode transactions that fail: decode the transaction, identify the depositor and token, and transfer the tokens back to the depositor.
2. For `WithdrawCollateral` slow mode transactions that fail: refund the slow mode fee to the sender.
3. Alternatively, do not delete the slow mode transaction slot before execution; only delete it on success, and provide a separate cancellation path that validates the failure reason before charging any fee.

---

### Proof of Concept

1. Alice calls `depositCollateralWithReferral(aliceSubaccount, productId=5, amount=1000e6)`.
   - 1000 USDC is transferred from Alice to the `Endpoint` contract.
   - A `DepositCollateral` slow mode transaction is queued at index `N` with `executableAt = now + 3 days`.
2. The protocol owner submits a `DelistProduct` slow mode transaction for `productId=5` and it is processed by the sequencer.
3. Three days later, anyone calls `executeSlowModeTransaction()` (or the sequencer includes `ExecuteSlowMode`).
4. `_executeSlowModeTransaction` deletes slot `N` from `slowModeTxs` and calls `this.processSlowModeTransaction(alice, depositTx)`.
5. Inside `processSlowModeTransactionImpl`, `clearinghouse.depositCollateral(txn)` reverts because product 5 is delisted.
6. The catch block fires. `gasleft()` is well above the threshold, so no `invalid()` is triggered.
7. The comment `// try return funds now removed` is reached. Nothing happens.
8. Alice's 1000 USDC is permanently locked in the `Endpoint` contract. There is no function to recover it.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/Endpoint.sol (L144-166)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Endpoint.sol (L193-227)
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
```

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```
