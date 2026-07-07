### Title
Slow-mode `DepositCollateral` transaction permanently deleted before execution success is confirmed, locking user funds on failure — (`core/contracts/Endpoint.sol`)

---

### Summary

In `Endpoint._executeSlowModeTransaction`, the slow-mode transaction record is **deleted from the queue before execution is attempted**. If `processSlowModeTransaction` reverts for any non-OOG reason, the failure is silently swallowed and the transaction is permanently gone. For `DepositCollateral` slow-mode transactions, user funds are already held in the clearinghouse at this point. A failed execution leaves those funds permanently stuck — not credited to any subaccount — with no recovery path. The comment `// try return funds now removed` in the catch block explicitly confirms that a fund-return mechanism was previously present and was deliberately removed.

---

### Finding Description

`Endpoint.depositCollateralWithReferral` transfers user funds to the clearinghouse immediately upon call, then enqueues a `DepositCollateral` slow-mode transaction to credit the subaccount later: [1](#0-0) 

The subaccount credit only materialises when the slow-mode transaction is later executed via `_executeSlowModeTransaction`. Inside that function, the transaction is **deleted from the queue before execution is attempted**: [2](#0-1) 

The critical sequence is:

1. `delete slowModeTxs[_slowModeConfig.txUpTo++]` — the record is permanently erased (line 194).
2. `try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch { … }` — execution is attempted inside a try/catch that silently swallows non-OOG reverts (lines 207–227).
3. The catch block contains only the comment `// try return funds now removed`, confirming that a fund-return path existed and was removed.

If `processSlowModeTransaction` reverts (e.g., because `clearinghouse.depositCollateral` fails), the outcome is:

- The slow-mode transaction record no longer exists in `slowModeTxs`.
- The user's funds are held in the clearinghouse but are not credited to their subaccount.
- There is no admin function, no retry mechanism, and no re-queue path — the funds are permanently locked.

This is structurally identical to the ZetaChain bug: a "completion" state update (`delete` / `IsAbortRefunded = true`) is applied unconditionally before confirming the underlying operation succeeded, and the absence of a recovery path makes the loss permanent.

---

### Impact Explanation

Any user who submits a `DepositCollateral` slow-mode transaction that subsequently fails during sequencer processing loses their deposited collateral permanently. The funds sit in the clearinghouse contract with no subaccount owner and no mechanism to reclaim them. The deleted queue entry cannot be reconstructed by any on-chain actor.

---

### Likelihood Explanation

`clearinghouse.depositCollateral` can revert if the target product is delisted between the time the slow-mode transaction is submitted and the time it is executed (a window of at least `SLOW_MODE_TX_DELAY` seconds). It can also revert due to any validation failure introduced by a contract upgrade during that window. The `depositCollateralWithReferral` path is a standard user-facing entry point callable by any unprivileged address, making the precondition reachable without any special privilege.

---

### Recommendation

Restore the fund-return mechanism that was previously present in the catch block. On a non-OOG revert of `processSlowModeTransaction` for a `DepositCollateral` transaction, the clearinghouse should transfer the deposited amount back to the original depositor. Alternatively, delay the `delete` until after a successful execution:

```solidity
// Only delete after confirmed success
try this.processSlowModeTransaction(txn.sender, txn.tx) {
    delete slowModeTxs[_slowModeConfig.txUpTo];
} catch {
    // handle OOG; otherwise refund deposited funds
}
_slowModeConfig.txUpTo++;
```

---

### Proof of Concept

1. User calls `depositCollateralWithReferral(subaccount, productId, amount, ref)`.
   - `handleDepositTransfer` moves `amount` tokens from the user to the clearinghouse. [3](#0-2) 
   - A `DepositCollateral` slow-mode transaction is enqueued at index `N`. [4](#0-3) 

2. Between submission and execution (≥ `SLOW_MODE_TX_DELAY`), `productId` is delisted or any other condition arises that causes `clearinghouse.depositCollateral` to revert.

3. The sequencer (or any caller after the timeout) triggers `_executeSlowModeTransaction`.
   - `delete slowModeTxs[N]` fires unconditionally — the record is gone. [5](#0-4) 
   - `processSlowModeTransaction` reverts; the catch block swallows the error. [6](#0-5) 

4. Result: `amount` tokens are held by the clearinghouse, credited to no subaccount, with no on-chain recovery path. The comment `// try return funds now removed` confirms the intentional removal of the only mitigation that existed. [7](#0-6)

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
