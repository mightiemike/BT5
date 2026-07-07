### Title
Silent Failure in `_executeSlowModeTransaction` Permanently Locks Deposited Funds After Queue Deletion — (`core/contracts/Endpoint.sol`)

---

### Summary

`_executeSlowModeTransaction` in `Endpoint.sol` deletes a slow mode transaction from the queue **before** attempting to execute it, then wraps execution in a `try/catch` that silently discards all errors. For `DepositCollateral` slow mode transactions, user tokens are already held by the `Endpoint` contract at execution time. If `processSlowModeTransaction` reverts, the slow mode entry is permanently gone, the user's balance is never credited, and there is no retry path. An inline comment — `// try return funds now removed` — explicitly acknowledges that a fund-return mechanism was deliberately removed, leaving the silent-failure path unmitigated.

---

### Finding Description

In `_executeSlowModeTransaction`:

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];   // ← queue entry destroyed first
...
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed              // ← acknowledged, unresolved
}
``` [1](#0-0) 

The queue entry is deleted at line 194 unconditionally. Execution is attempted inside a `try/catch` at line 207. Any revert inside `processSlowModeTransaction` is silently swallowed (unless the gas heuristic triggers `invalid()`).

For `DepositCollateral`, the user's tokens are transferred from the caller into the `Endpoint` contract inside `depositCollateralWithReferral` **before** the slow mode transaction is even queued:

```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
// slow mode tx queued after this
``` [2](#0-1) 

When the slow mode tx is later executed, `processSlowModeTransactionImpl` calls `clearinghouse.depositCollateral(txn)`: [3](#0-2) 

If `clearinghouse.depositCollateral` reverts — for example because `_decimals(productId)` fails after a product configuration change, or because `require(txn.amount <= INT128_MAX)` is violated by a race between submission and execution — the `try/catch` absorbs the revert. The slow mode entry is already deleted. The user's tokens remain in the `Endpoint` contract with no on-chain balance credit and no mechanism to resubmit or recover funds.

---

### Impact Explanation

A user whose `DepositCollateral` slow mode transaction fails silently loses their deposited tokens permanently. The tokens are held by `Endpoint` but no `spotEngine` balance is ever credited. There is no recovery function, no resubmission path, and no event emitted on failure. The asset delta is: user loses `amount` tokens; `Endpoint` holds them indefinitely with no corresponding liability.

---

### Likelihood Explanation

The trigger requires `clearinghouse.depositCollateral` to revert after the tokens have already been transferred. Concrete paths include: a product being reconfigured or its token address zeroed between deposit submission and slow mode execution (the three-day `SLOW_MODE_TX_DELAY` window creates a meaningful gap); an amount that passes the pre-transfer `isValidDepositAmount` check but fails the `require(txn.amount <= INT128_MAX)` guard inside `clearinghouse.depositCollateral` due to differing code paths; or any future revert introduced in `clearinghouse.depositCollateral` by an upgrade. The three-day delay window and the explicit removal of the fund-return mechanism (noted in the comment) make this a realistic, non-theoretical risk.

---

### Recommendation

1. **Short term:** Restore a fund-return path inside the `catch` block for `DepositCollateral` slow mode transactions — credit the user's `spotEngine` balance directly, or transfer tokens back to the original sender, rather than leaving them stranded.
2. **Short term:** Move the `delete slowModeTxs[...]` operation to **after** successful execution, so a failed transaction can be retried.
3. **Long term:** Audit all slow mode transaction types for state mutations that occur before execution and cannot be undone on failure.

---

### Proof of Concept

1. User calls `Endpoint.depositCollateral(subaccountName, productId, amount)`.
2. `depositCollateralWithReferral` transfers `amount` tokens from the user into `Endpoint` via `handleDepositTransfer` and queues a `DepositCollateral` slow mode tx. [4](#0-3) 
3. Between submission and the three-day execution window, the product's token configuration is changed such that `clearinghouse._decimals(productId)` reverts (e.g., token address zeroed).
4. Anyone calls `executeSlowModeTransaction()` (permissionless after timeout). [5](#0-4) 
5. `_executeSlowModeTransaction` deletes the slow mode entry at line 194, then calls `processSlowModeTransaction` inside `try/catch`.
6. `clearinghouse.depositCollateral` reverts; the `catch` block discards the error and execution continues normally. [6](#0-5) 
7. Result: user's tokens are held by `Endpoint`, no `spotEngine` balance is credited, the slow mode entry is gone, and there is no on-chain path to recover the funds.

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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L209-217)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
```
