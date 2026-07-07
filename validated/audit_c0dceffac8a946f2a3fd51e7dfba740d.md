### Title
Slow Mode Fee Permanently Lost When Execution Fails Silently — (`File: core/contracts/Endpoint.sol` / `core/contracts/EndpointTx.sol`)

---

### Summary

Users who submit slow mode transactions pay `SLOW_MODE_FEE` upfront at submission time. If the queued transaction later fails during execution, the fee is silently consumed with no refund. A removed refund mechanism (evidenced by a code comment) confirms this is a known gap. Any state change in the 3-day window between submission and execution can trigger silent failure, permanently burning the user's fee.

---

### Finding Description

The slow mode flow has two distinct phases separated by a mandatory 3-day delay (`SLOW_MODE_TX_DELAY`):

**Phase 1 — Submission (fee charged immediately):**

In `submitSlowModeTransactionImpl`, for any transaction type not in the admin-only or `DepositInsurance` branches, the fee is charged unconditionally at submission time:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [1](#0-0) 

The transaction is then stored in the queue with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY`. [2](#0-1) 

**Phase 2 — Execution (silent failure, no refund):**

In `_executeSlowModeTransaction`, execution is wrapped in a `try/catch`. If `processSlowModeTransaction` reverts for any reason, the catch block silently discards the error. The comment `// try return funds now removed` explicitly confirms that a refund path previously existed and was deleted:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [3](#0-2) 

When execution fails, `sequencerFee[QUOTE_PRODUCT_ID]` retains the fee increment from Phase 1, and the user's spot balance is never restored. [4](#0-3) 

---

### Impact Explanation

The user's quote-token spot balance is permanently decremented by `SLOW_MODE_FEE` with no corresponding benefit. The exact corrupted state delta is:

- `spotEngine` balance for `sender` decremented by `SLOW_MODE_FEE` (via `chargeFee` → `spotEngine.updateBalance`)
- `sequencerFee[QUOTE_PRODUCT_ID]` incremented by `SLOW_MODE_FEE`
- No reversal on failure

This is a direct, concrete asset loss for the user in quote tokens.

---

### Likelihood Explanation

The 3-day mandatory delay (`SLOW_MODE_TX_DELAY`) between submission and execution creates a wide window for state changes that can cause execution to revert:

- A `WithdrawCollateral` slow mode tx can fail if the account becomes unhealthy due to price movements during the 3-day window.
- A `LinkSigner` or `ClaimBuilderFee` tx can fail if the subaccount is sanctioned between submission and execution (`requireUnsanctioned` is checked at submission but protocol state can change).
- Any slow mode tx targeting a product that gets delisted (`DelistProduct`) between submission and execution will revert.

These are realistic, non-adversarial conditions that any ordinary user can encounter. No attacker action is required — the user is the victim of their own legitimately submitted transaction failing due to changed protocol state.

---

### Recommendation

Restore the fee refund path that was previously removed. When `processSlowModeTransaction` fails in the catch block, the `SLOW_MODE_FEE` should be credited back to the original sender's spot balance. Alternatively, charge the fee only upon successful execution rather than at submission time.

---

### Proof of Concept

1. User calls `submitSlowModeTransaction` with a `WithdrawCollateral` transaction.
2. `submitSlowModeTransactionImpl` charges `SLOW_MODE_FEE` from the user's quote balance immediately. [1](#0-0) 
3. Transaction is queued with `executableAt = now + 3 days`. [5](#0-4) 
4. During the 3-day window, the user's account health deteriorates (e.g., perp position moves against them), making the withdrawal invalid.
5. Sequencer (or anyone) calls `executeSlowModeTransaction` after the delay.
6. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(...)` inside a `try/catch`. [6](#0-5) 
7. `clearinghouse.withdrawCollateral` reverts due to insufficient health.
8. The catch block silently swallows the revert. The comment `// try return funds now removed` confirms no refund is issued. [7](#0-6) 
9. User's `SLOW_MODE_FEE` is permanently lost. The withdrawal never executes.

### Citations

**File:** core/contracts/EndpointTx.sol (L130-141)
```text
    function chargeFee(bytes32 sender, int128 fee) internal {
        chargeFee(sender, fee, QUOTE_PRODUCT_ID);
    }

    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
```

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
