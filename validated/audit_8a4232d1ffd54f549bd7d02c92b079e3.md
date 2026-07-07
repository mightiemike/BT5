### Title
Silent Failure in Slow-Mode Transaction Processing Permanently Locks Deposited Collateral — (File: `core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint._executeSlowModeTransaction` deletes a slow-mode transaction from the queue **before** attempting to process it. If processing reverts, the catch block is empty — a comment explicitly acknowledges that fund-return logic was removed. Because `depositCollateralWithReferral` transfers tokens to the contract **before** queuing the slow-mode transaction, any revert during processing permanently locks the deposited collateral with no recovery path.

---

### Finding Description

**Step 1 — Tokens transferred before slow-mode tx is queued.**

In `depositCollateralWithReferral`, `handleDepositTransfer` moves tokens from the caller into the contract, then a `SlowModeTx` is pushed to the queue: [1](#0-0) 

**Step 2 — Slow-mode tx is deleted from the queue before processing.**

`_executeSlowModeTransaction` removes the entry with `delete` and increments the pointer **before** the `try` call: [2](#0-1) 

**Step 3 — Empty catch block with explicit acknowledgement that recovery was removed.**

If `processSlowModeTransaction` reverts (not out-of-gas), the catch block silently swallows the error. The comment `// try return funds now removed` is a direct admission that the fund-return logic was intentionally deleted: [3](#0-2) 

Because the tx was already deleted from `slowModeTxs` at line 194, it cannot be retried. The tokens transferred in step 1 remain in the contract with no accounting entry and no recovery mechanism.

---

### Impact Explanation

**Impact: High.**

Any collateral deposited via `depositCollateral` / `depositCollateralWithReferral` that corresponds to a slow-mode transaction that later reverts is permanently locked. The user's on-chain balance is never credited, the slow-mode entry is gone, and there is no admin escape hatch visible in the contract to recover the funds. This is a direct, irreversible loss of user assets.

---

### Likelihood Explanation

**Likelihood: Low.**

A revert during `processSlowModeTransaction` is required. Realistic triggers include:

- The depositing address is added to the sanctions list between the time of deposit and the time the sequencer processes the slow-mode tx (sanctions are checked again during processing).
- The product is delisted or its minimum deposit threshold is raised between deposit and processing.
- A delegatecall failure in `EndpointTx.processSlowModeTransactionImpl` due to an upgrade mismatch.

None of these are everyday events, but they are all plausible protocol-lifecycle events, making the likelihood low but non-zero.

---

### Recommendation

Restore the fund-return logic in the catch block. When a `DepositCollateral` slow-mode transaction fails, the contract should transfer the deposited tokens back to the original sender. A minimal fix:

```solidity
} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // Decode tx type; if DepositCollateral, refund the tokens to txn.sender
    _refundFailedDeposit(txn.sender, txn.tx);
}
```

Alternatively, adopt a two-phase pattern: do not transfer tokens until the slow-mode transaction is successfully processed.

---

### Proof of Concept

1. Alice calls `depositCollateral("default", USDC_PRODUCT_ID, 1000e6)`.
2. `depositCollateralWithReferral` passes the validity check, calls `handleDepositTransfer` — 1000 USDC moves from Alice to the `Endpoint` contract.
3. A `SlowModeTx` for `DepositCollateral` is pushed to `slowModeTxs[N]`.
4. Before the sequencer processes the tx, Alice's address is added to the sanctions list.
5. The sequencer calls `submitTransactionsChecked` → `processTransaction` → `_executeSlowModeTransaction`.
6. Line 194: `delete slowModeTxs[N]` — the entry is gone.
7. `processSlowModeTransaction` reverts (sanctions check fails inside `EndpointTx`).
8. The catch block executes; gas is sufficient so `invalid()` is not triggered; the comment `// try return funds now removed` is reached and nothing happens.
9. Alice's 1000 USDC is permanently locked in the contract. Her subaccount balance was never credited. The slow-mode entry no longer exists. [4](#0-3)

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

**File:** core/contracts/Endpoint.sol (L185-229)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
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
    }
```
