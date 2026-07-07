### Title
Slow-Mode Fee Permanently Lost When Queued Transaction Fails During Execution — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The slow-mode submission fee is charged at queue time in `submitSlowModeTransactionImpl`, but is never refunded when the queued transaction fails during deferred execution. A removed refund path (evidenced by the comment `// try return funds now removed`) confirms this is a live gap: users pay for operations that never succeed.

---

### Finding Description

In `EndpointTx.sol`, every user-initiated slow-mode transaction (e.g., `WithdrawCollateral`, `WithdrawCollateralV2`, `LiquidateSubaccount`) triggers `chargeSlowModeFee` and increments `slowModeFees` at submission time, before the transaction is queued: [1](#0-0) 

The transaction is then stored in `slowModeTxs` with a hardcoded 3-day execution delay: [2](#0-1) 

When the queued transaction is later executed via `_executeSlowModeTransaction` in `Endpoint.sol`, the call to `processSlowModeTransaction` is wrapped in a `try/catch`. If the inner call reverts for any reason (e.g., the user's balance has since dropped below the withdrawal amount), the catch block silently discards the failure. The comment `// try return funds now removed` at line 226 explicitly confirms that a fee-refund path previously existed here and was deliberately removed: [3](#0-2) 

The slow-mode fee charged at submission is never touched again in the failure path. It remains credited to `slowModeFees` (the protocol), and the user receives nothing back.

---

### Impact Explanation

A user who submits a slow-mode `WithdrawCollateral` transaction pays the slow-mode fee upfront. If the withdrawal fails during execution (e.g., because a concurrent perp loss reduced their balance below the requested amount in the intervening 3 days), the user loses the slow-mode fee and receives no withdrawal. The user has paid for an operation they do not possess — the exact impact class of the reference bug. The corrupted state delta is: user's quote balance is permanently reduced by `SLOW_MODE_FEE`; `slowModeFees` is permanently increased by the same amount; no corresponding operation was executed.

---

### Likelihood Explanation

The 3-day `SLOW_MODE_TX_DELAY` creates a wide window for state divergence between submission and execution. Any user who:
- submits a `WithdrawCollateral` slow-mode transaction while their balance is sufficient, then
- suffers a perp loss or liquidation that reduces their balance below the withdrawal amount before execution

will trigger this path. This is a realistic, unprivileged scenario requiring no special access. The entry point (`submitSlowModeTransaction`) is publicly callable by any non-sanctioned address. [4](#0-3) 

---

### Recommendation

Restore a fee-refund path in the catch block of `_executeSlowModeTransaction`. The `SlowModeTx` struct should be extended to record the fee payer's address and the fee amount at submission time. On a caught failure, the stored fee should be credited back to the original sender rather than retained in `slowModeFees`.

---

### Proof of Concept

1. User holds 100 USDC in their subaccount.
2. User calls `submitSlowModeTransaction` with a `WithdrawCollateral` tx for 100 USDC.
3. `chargeSlowModeFee` deducts `SLOW_MODE_FEE` from the user; `slowModeFees += SLOW_MODE_FEE`.
4. The tx is stored in `slowModeTxs` with `executableAt = now + 3 days`.
5. Over the next 3 days, a perp loss reduces the user's balance to 50 USDC.
6. `_executeSlowModeTransaction` is called (by sequencer or anyone after the delay).
7. `this.processSlowModeTransaction(txn.sender, txn.tx)` is called inside `try`.
8. Inside, `withdrawCollateral` reverts — the user no longer has 100 USDC.
9. The `catch` block fires; the comment `// try return funds now removed` confirms no refund occurs.
10. The user has lost `SLOW_MODE_FEE` and received no withdrawal. [1](#0-0) [3](#0-2)

### Citations

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L374-384)
```text
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

**File:** core/contracts/Endpoint.sol (L205-227)
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
```
