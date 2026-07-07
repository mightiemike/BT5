### Title
Silent Swallowing of Slow Mode Transaction Failures Causes Permanent Fund Loss and Irrecoverable Withdrawal Drops — (`core/contracts/Endpoint.sol`)

---

### Summary

`_executeSlowModeTransaction` in `Endpoint.sol` deletes the slow mode transaction from the queue **before** attempting execution, then wraps execution in a `try/catch` whose `catch` block silently discards all non-OOG failures. A developer comment on line 226 — `// try return funds now removed` — explicitly confirms that a prior fund-recovery path was intentionally stripped out. The result is that any slow mode transaction that reverts during execution is permanently destroyed with no on-chain recovery mechanism. For `DepositCollateral` slow mode transactions, the user's ERC-20 funds have already been transferred to the contract before the slow mode entry is queued; if the deferred execution later fails, those funds are irrecoverably stranded. For `WithdrawCollateral` slow mode transactions, the withdrawal is silently dropped and the user forfeits the slow mode fee.

---

### Finding Description

**Root cause — `Endpoint.sol` lines 193–227:**

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];   // ← deleted BEFORE execution

// ...
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed          ← recovery path explicitly removed
}
``` [1](#0-0) 

The slow mode entry is erased at line 194 unconditionally. If `processSlowModeTransaction` reverts for any reason other than OOG, the `catch` block silently discards the error and execution continues. There is no re-queue, no refund, and no event emitted.

**Fund-loss path — `DepositCollateral`:**

`depositCollateralWithReferral` transfers the user's ERC-20 tokens to the contract **before** enqueuing the slow mode entry:

```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
// ...
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({ ... });
``` [2](#0-1) 

If the deferred `clearinghouse.depositCollateral` call later reverts (e.g., due to a state change in the spot engine between deposit and the 3-day execution window, an arithmetic edge case in `_updateBalanceNormalized`, or any future upgrade that tightens validation), the catch block swallows the revert, the slow mode entry is gone, and the user's tokens remain in the clearinghouse with no corresponding balance credit and no way to recover them.

**Silent-drop path — `WithdrawCollateral`:**

A user submits a `WithdrawCollateral` slow mode transaction and pays the slow mode fee. Over the mandatory 3-day delay, market movements can push the user's health below the initial threshold. When `clearinghouse.withdrawCollateral` is eventually called, it reaches:

```solidity
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [3](#0-2) 

This reverts inside `processSlowModeTransaction`. The outer `catch` swallows it. The slow mode entry is already deleted. The user loses the slow mode fee and the withdrawal is permanently dropped with no notification.

**Permissionless trigger — `executeSlowModeTransaction`:**

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    nSubmissions += 1;
    slowModeConfig = _slowModeConfig;
}
``` [4](#0-3) 

This function is `external` with no access control. Any unprivileged caller can invoke it. An adversary who monitors on-chain health can wait until a target user's health falls below initial and then call `executeSlowModeTransaction` to deliberately trigger the silent failure, destroying the user's queued withdrawal and forfeiting their slow mode fee.

---

### Impact Explanation

1. **Permanent fund loss (DepositCollateral path):** User ERC-20 tokens are transferred to the clearinghouse before the slow mode entry is created. If the deferred execution fails for any reason, the tokens are stranded in the contract with no balance credit and no recovery path. The developer comment `// try return funds now removed` confirms the prior mitigation was deliberately removed.

2. **Irrecoverable withdrawal drop (WithdrawCollateral path):** A user's queued withdrawal is silently destroyed if health falls below initial during the 3-day window. The user loses the slow mode fee and must resubmit, with no on-chain indication of what happened.

3. **Permissionless griefing:** Because `executeSlowModeTransaction` is callable by anyone, an adversary can time the execution of a victim's slow mode transaction to coincide with a health failure, reliably triggering the silent drop.

---

### Likelihood Explanation

- The 3-day slow mode delay (`SLOW_MODE_TX_DELAY`) is a mandatory window during which market conditions can change substantially, making health-check failures for `WithdrawCollateral` a realistic occurrence.
- `executeSlowModeTransaction` is permissionless, so any caller can trigger execution at an adversarially chosen moment.
- The `DepositCollateral` fund-loss path requires a state change between deposit and execution; while less frequent, the 3-day window and the explicit removal of the recovery path make this a latent, non-theoretical risk.

---

### Recommendation

1. **Restore a fund-return path for `DepositCollateral`:** When a `DepositCollateral` slow mode transaction fails, refund the deposited tokens to the original sender rather than leaving them stranded.

2. **Re-queue or emit on failure:** For `WithdrawCollateral` and other slow mode types, either re-enqueue the transaction or emit a structured failure event so users and off-chain systems can detect and respond to silent drops.

3. **Delete after execution, not before:** Move `delete slowModeTxs[...]` to after the `try` block succeeds, or use a separate "failed" mapping to allow retries.

4. **Restrict `executeSlowModeTransaction` or add a cooldown:** Prevent adversarial timing of execution by requiring the caller to be the original sender or the sequencer, or by enforcing a minimum gas/health precondition check before deletion.

---

### Proof of Concept

**Scenario A — DepositCollateral fund loss:**

1. User calls `depositCollateralWithReferral(subaccount, productId, amount, referral)`.
2. `handleDepositTransfer` transfers `amount` tokens from the user to the clearinghouse.
3. A `DepositCollateral` slow mode entry is enqueued with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY`.
4. Before execution, a protocol state change (e.g., spot engine upgrade, product reconfiguration) causes `clearinghouse.depositCollateral` to revert.
5. After 3 days, anyone calls `executeSlowModeTransaction()`.
6. `delete slowModeTxs[txUpTo++]` removes the entry.
7. `try this.processSlowModeTransaction(...)` reverts; the `catch` block swallows it.
8. User's tokens remain in the clearinghouse. No balance is credited. No refund is issued. The entry is gone.

**Scenario B — WithdrawCollateral griefing:**

1. User submits a `WithdrawCollateral` slow mode transaction, paying the slow mode fee.
2. Over the 3-day delay, the user's collateral value drops (e.g., due to funding payments or price movement), pushing health below initial.
3. Adversary monitors on-chain health and calls `executeSlowModeTransaction()` at the moment health is negative.
4. `clearinghouse.withdrawCollateral` reverts at `require(getHealth(...) >= 0, ERR_SUBACCT_HEALTH)`.
5. The `catch` block swallows the revert. The slow mode entry is already deleted.
6. User loses the slow mode fee. Withdrawal is permanently dropped. No event is emitted.

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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Clearinghouse.sol (L419-419)
```text
        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
