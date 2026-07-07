### Title
Slow-Mode `DepositCollateral` Failure Permanently Locks User Tokens in Clearinghouse — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` in `Endpoint.sol` transfers user tokens to the `Clearinghouse` **before** queuing the slow-mode `DepositCollateral` transaction. If that queued transaction later fails during execution, the catch block in `_executeSlowModeTransaction` silently discards it with no recovery path — the tokens remain in the `Clearinghouse` but the user's `SpotEngine` balance is never credited. The in-code comment `// try return funds now removed` at line 226 explicitly acknowledges that a prior recovery mechanism was deleted, leaving a permanent fund-lock gap.

---

### Finding Description

`depositCollateralWithReferral` (Endpoint.sol lines 123–167) performs two sequential operations:

1. **Immediate token transfer** — `handleDepositTransfer` moves tokens from `msg.sender` into the `Clearinghouse` contract (lines 144–148).
2. **Deferred balance credit** — a `SlowModeTx` of type `DepositCollateral` is pushed onto the queue (lines 152–165) with a hardcoded 3-day delay (`SLOW_MODE_TX_DELAY`). [1](#0-0) 

The balance credit only happens when `_executeSlowModeTransaction` later calls `processSlowModeTransactionImpl`, which calls `clearinghouse.depositCollateral(txn)`. [2](#0-1) 

`_executeSlowModeTransaction` wraps the call in a try/catch:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [3](#0-2) 

If `processSlowModeTransaction` reverts for **any** reason other than an out-of-gas that the heuristic catches, the catch block silently drops the transaction. The comment `// try return funds now removed` confirms that a prior refund path was intentionally deleted. The tokens already transferred to the `Clearinghouse` are never returned and the user's `SpotEngine` balance is never incremented — a permanent desynchronization between on-chain token custody and protocol accounting.

The out-of-gas heuristic itself is acknowledged as imperfect:

> *"having gasleft() <= gasRemaining / 2 buys us 44 nested calls before we miss out of gas errors; 1/2 ~= (63/64)**44 — this is good enough for our purposes"* [4](#0-3) 

Beyond OOG, `clearinghouse.depositCollateral` can revert due to business-logic conditions that change during the 3-day window (e.g., product configuration changes, utilization constraints, or any future guard added to the deposit path). Because the slow-mode queue is strictly sequential (`txUpTo` advances regardless of success), the failed entry is consumed and cannot be replayed. [5](#0-4) 

---

### Impact Explanation

**High.** User tokens are permanently locked inside the `Clearinghouse` contract. The `SpotEngine` balance for the depositing subaccount is never credited, so the user has no collateral to trade against or withdraw. There is no admin escape hatch or replay mechanism — the slow-mode entry is deleted from `slowModeTxs` before execution is attempted, and the catch block provides no refund.

---

### Likelihood Explanation

**Low.** The most realistic trigger is an out-of-gas exception that slips past the `gasleft()` heuristic, or a revert inside `clearinghouse.depositCollateral` caused by a state change (product config, utilization cap) that occurs during the mandatory 3-day delay. Both are uncommon in normal operation but are non-zero probability events over the protocol's lifetime, especially as product configurations evolve.

---

### Recommendation

1. **Restore a refund path in the catch block** for `DepositCollateral` slow-mode transactions. When `processSlowModeTransaction` fails and the transaction type is `DepositCollateral`, transfer the deposited tokens back to the original sender from the `Clearinghouse`.
2. **Alternatively**, move the `handleDepositTransfer` call out of `depositCollateralWithReferral` and into `processSlowModeTransactionImpl` so tokens are only transferred at the moment the balance is credited — eliminating the window of desynchronization entirely.
3. **At minimum**, add a dedicated recovery function callable by the depositor (with appropriate guards) to reclaim tokens for failed slow-mode deposits, analogous to the "send message back to L1" fix described in the reference report.

---

### Proof of Concept

1. User calls `Endpoint.depositCollateral(subaccountName, productId, amount)`.
2. `depositCollateralWithReferral` passes all checks, calls `handleDepositTransfer` — tokens move from user → `Clearinghouse`. A `SlowModeTx` of type `DepositCollateral` is queued with `executableAt = block.timestamp + 3 days`. [6](#0-5) 
3. During the 3-day window, a state change occurs that causes `clearinghouse.depositCollateral` to revert (e.g., OOG on a congested block, or a product-level guard added by an upgrade).
4. After 3 days, anyone calls `executeSlowModeTransaction()`. `_executeSlowModeTransaction` deletes the entry from `slowModeTxs` and calls `processSlowModeTransaction` inside a try/catch. [5](#0-4) 
5. `processSlowModeTransaction` reverts. The catch block fires. `gasleft()` is above the threshold, so `invalid()` is not triggered. Execution falls through to `// try return funds now removed` — nothing happens. [7](#0-6) 
6. The slow-mode entry is permanently gone. The user's `SpotEngine` balance was never incremented. The tokens remain in the `Clearinghouse` with no recovery path. The user's funds are locked.

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

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
```

**File:** core/contracts/Endpoint.sol (L207-227)
```text
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
