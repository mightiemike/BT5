### Title
Deposited Collateral Tokens Permanently Stuck When Slow Mode `DepositCollateral` Transaction Fails - (`File: core/contracts/Endpoint.sol`)

### Summary

When a user calls `depositCollateralWithReferral`, tokens are transferred from the user to the `Endpoint` contract before the slow mode transaction is queued. If the queued `DepositCollateral` slow mode transaction later fails during execution, the `catch` block silently discards the error without returning the user's tokens — permanently locking them in the contract. The codebase itself contains a comment explicitly acknowledging the removal of the fund-return logic: `// try return funds now removed`.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` first pulls tokens from the user via `handleDepositTransfer`, then enqueues a `SlowModeTx` with a 3-day delay: [1](#0-0) 

When `executeSlowModeTransaction` is later called (by anyone), it invokes `_executeSlowModeTransaction`, which wraps `processSlowModeTransaction` in a `try/catch`: [2](#0-1) 

The `catch` block does **not** return the deposited tokens to the user. The comment on line 226 — `// try return funds now removed` — explicitly confirms that fund-return logic was intentionally deleted, leaving no recovery path for the user's tokens if the inner call reverts.

`processSlowModeTransaction` delegates to `processSlowModeTransactionImpl`, which calls `clearinghouse.depositCollateral(txn)` for `DepositCollateral` transactions: [3](#0-2) 

If `clearinghouse.depositCollateral` reverts for any reason (e.g., the product was delisted during the 3-day slow mode window, a product configuration change, or any other clearinghouse-side revert), the tokens already held by `Endpoint` are never credited to the user and cannot be recovered.

---

### Impact Explanation

**Impact: High.** User-deposited ERC-20 collateral tokens are permanently locked inside the `Endpoint` contract with no recovery mechanism. The user loses their full deposited amount. There is no admin function to rescue stuck tokens for individual users.

---

### Likelihood Explanation

**Likelihood: Low.** The 3-day slow mode delay creates a window during which a product can be delisted via a sequencer-submitted `DelistProduct` transaction. A user who deposits collateral for a product that is subsequently delisted before their slow mode tx executes will have their tokens permanently stuck. This is an edge case but is a realistic operational scenario.

---

### Recommendation

In the `catch` block of `_executeSlowModeTransaction`, restore the fund-return logic for `DepositCollateral` transactions. When the slow mode tx type is `DepositCollateral`, decode the transaction, retrieve the deposited token and amount, and transfer them back to the original depositor (`txn.sender`). Alternatively, record the stuck amount in a claimable mapping so the user can withdraw it themselves.

---

### Proof of Concept

1. User calls `depositCollateral(subaccountName, productId, amount)` on `Endpoint`.
2. `handleDepositTransfer` pulls `amount` of the product's token from the user into `Endpoint`. [4](#0-3) 
3. A `SlowModeTx` of type `DepositCollateral` is queued with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days). [5](#0-4) 
4. During the 3-day window, the sequencer submits a `DelistProduct` transaction for `productId`, causing `clearinghouse.depositCollateral` to revert for that product.
5. After 3 days, anyone calls `executeSlowModeTransaction()`.
6. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(...)` in a `try`. The call reverts because the product is delisted.
7. The `catch` block fires. No tokens are returned. The comment `// try return funds now removed` confirms the absence of recovery logic. [6](#0-5) 
8. The user's tokens remain permanently locked in `Endpoint` with no recovery path.

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
