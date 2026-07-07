### Title
Slow Mode Fee Not Returned to User When Transaction Fails During Execution — (`core/contracts/Endpoint.sol`)

---

### Summary

When a user submits a slow mode transaction, they pay `SLOW_MODE_FEE` ($1) upfront via a real ERC20 transfer. The transaction is queued with a 3-day delay. If the transaction fails during execution, the fee is silently consumed and never returned. The codebase itself contains the comment `// try return funds now removed` at the exact catch site, confirming that a refund path previously existed and was deliberately deleted.

---

### Finding Description

In `submitSlowModeTransactionImpl`, for all non-admin transaction types, the user's quote token is pulled immediately via `chargeSlowModeFee`:

```solidity
chargeSlowModeFee(_getQuote(), sender);
slowModeFees += SLOW_MODE_FEE;
```

`chargeSlowModeFee` performs a real ERC20 `safeTransferFrom` into the Endpoint contract:

```solidity
token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
```

The transaction is then stored in `slowModeTxs` with a 3-day execution delay (`SLOW_MODE_TX_DELAY`). When executed via `_executeSlowModeTransaction`, the call is wrapped in a try/catch on non-hardhat chains:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    // ...
    // try return funds now removed
}
```

On failure, the catch block does nothing. The `slowModeFees` counter is only ever incremented — it is never decremented, and there is no function that returns the ERC20 fee to the original submitter. The `DumpFees` slow mode transaction only claims `sequencerFee` (internal balance-level fees), not `slowModeFees` (the actual ERC20 tokens held by Endpoint).

The concrete failure scenario: a user submits a `WithdrawCollateral` slow mode transaction and pays $1. During the 3-day delay, their subaccount is liquidated or becomes unhealthy. When the slow mode transaction is executed, `clearinghouse.withdrawCollateral` reverts. The catch block silently discards the failure. The user's $1 is permanently locked in the Endpoint contract with no recovery path.

---

### Impact Explanation

Every user whose slow mode transaction fails during execution loses `SLOW_MODE_FEE` ($1 in quote token) with no recourse. The fee is held in the Endpoint contract and tracked in `slowModeFees`, but there is no function to return it to the original submitter. The corrupted state is: `slowModeFees` is inflated by $1 per failed transaction, and the user's wallet balance is permanently reduced by $1 per failed transaction, with no corresponding protocol service rendered.

---

### Likelihood Explanation

The 3-day delay between submission and execution creates a realistic window for state changes that cause execution failure. A `WithdrawCollateral` slow mode transaction will fail if the subaccount's health deteriorates (price movement, funding payments, or partial liquidation) during those 3 days. A `LinkSigner` or `ClaimBuilderFee` slow mode transaction can fail if the subaccount is deregistered or the builder is removed. These are normal protocol operations that any unprivileged user can trigger, and the 3-day delay makes state divergence between submission and execution a routine occurrence.

---

### Recommendation

Restore the fee refund path in the catch block of `_executeSlowModeTransaction`. When `processSlowModeTransaction` reverts, the Endpoint contract should transfer `clearinghouse.getSlowModeFee()` back to `txn.sender` and decrement `slowModeFees` accordingly. Alternatively, record the submitter address alongside each queued slow mode transaction so that a targeted refund can be issued on failure.

---

### Proof of Concept

1. User calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload.
2. `submitSlowModeTransactionImpl` executes: `chargeSlowModeFee(_getQuote(), sender)` pulls $1 from the user's wallet; `slowModeFees += SLOW_MODE_FEE` records it. [1](#0-0) 
3. The transaction is stored in `slowModeTxs` with `executableAt = block.timestamp + 3 days`. [2](#0-1) 
4. During the 3-day delay, the user's subaccount is liquidated and its balance drops to zero.
5. After 3 days, anyone calls `executeSlowModeTransaction`. `_executeSlowModeTransaction` fires the try/catch: [3](#0-2) 
6. `processSlowModeTransaction` → `clearinghouse.withdrawCollateral` reverts (unhealthy subaccount). The catch block is entered. The comment `// try return funds now removed` confirms no refund is issued.
7. `slowModeFees` remains inflated; the user's $1 is permanently locked in the Endpoint contract. `chargeSlowModeFee` transferred the token to `address(this)` with no corresponding credit or return path. [4](#0-3)

### Citations

**File:** core/contracts/EndpointTx.sol (L370-372)
```text
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```
