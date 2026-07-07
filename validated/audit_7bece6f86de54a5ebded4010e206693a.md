### Title
Sanctions Check Not Re-Enforced at Slow Mode Execution, Allowing Pre-Queued Withdrawal by Sanctioned User — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

`requireUnsanctioned` is enforced at slow mode **submission** time but is entirely absent at **execution** time. A user who queues a slow mode `WithdrawCollateral` transaction before being sanctioned can force its execution after the 3-day delay via the permissionless `executeSlowModeTransaction`, bypassing the protocol's sanctions enforcement.

---

### Finding Description

**Root cause — submission path enforces sanctions, execution path does not.**

In `submitSlowModeTransactionImpl`, after charging the slow mode fee, the check fires: [1](#0-0) 

```solidity
requireUnsanctioned(sender);
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({...});
```

However, in `processSlowModeTransactionImpl`, the `WithdrawCollateral` branch only calls `validateSender` and immediately invokes `clearinghouse.withdrawCollateral` — no sanctions re-check: [2](#0-1) 

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(...);
    validateSender(txn.sender, sender);          // address match only
    clearinghouse.withdrawCollateral(            // no requireUnsanctioned
        txn.sender, txn.productId, txn.amount, address(0), nSubmissions
    );
}
```

**Permissionless forced execution after delay.**

`executeSlowModeTransaction` is callable by any address with no access control or sanctions check: [3](#0-2) 

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    nSubmissions += 1;
    slowModeConfig = _slowModeConfig;
}
```

`_executeSlowModeTransaction` only checks the time delay, then calls `processSlowModeTransaction` — no sanctions gate anywhere in the execution chain: [4](#0-3) 

**There is no on-chain cancellation mechanism.** Once a slow mode transaction is queued, it cannot be removed except by execution.

**Attack path:**
1. Attacker (not yet sanctioned) calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload. `requireUnsanctioned(sender)` passes.
2. Transaction is queued: `executableAt = block.timestamp + SLOW_MODE_TX_DELAY`.
3. Attacker is added to the sanctions list.
4. After 3 days, attacker calls `executeSlowModeTransaction()`.
5. `processSlowModeTransactionImpl` reaches the `WithdrawCollateral` branch: `validateSender` passes (address matches), no sanctions check, `clearinghouse.withdrawCollateral` executes.
6. Funds are transferred to the attacker's subaccount and withdrawn.

---

### Impact Explanation

A sanctioned user can withdraw their full collateral balance from the protocol despite being on the sanctions list. The corrupted state delta is the complete collateral balance of the sanctioned subaccount being transferred out. This directly violates the protocol's compliance posture and the invariant that sanctioned addresses cannot move funds.

---

### Likelihood Explanation

**Medium.** The attacker must anticipate their own sanctioning and pre-submit the withdrawal. This is realistic for sophisticated actors aware of pending regulatory designations. The 3-day delay provides a reaction window, but the protocol has no on-chain cancellation primitive — the only operational defense would be for the sequencer to drain the subaccount balance before the delay expires, which is fragile and not guaranteed.

---

### Recommendation

Add `requireUnsanctioned(sender)` at the top of `processSlowModeTransactionImpl` (or at minimum within the `WithdrawCollateral` and `LinkSigner` branches) to enforce sanctions at execution time, mirroring the check already present at submission time: [5](#0-4) 

```solidity
// In processSlowModeTransactionImpl, before the txType dispatch:
requireUnsanctioned(sender);
```

---

### Proof of Concept

```solidity
// Step 1: attacker submits withdrawal before sanctioning
endpoint.submitSlowModeTransaction(
    abi.encodePacked(
        uint8(IEndpoint.TransactionType.WithdrawCollateral),
        abi.encode(IEndpoint.WithdrawCollateral({
            sender: attackerSubaccount,
            productId: QUOTE_PRODUCT_ID,
            amount: fullBalance,
            nonce: 0
        }))
    )
);
// requireUnsanctioned passes — attacker not yet sanctioned

// Step 2: attacker is added to sanctions list (off-chain event)

// Step 3: after SLOW_MODE_TX_DELAY seconds
endpoint.executeSlowModeTransaction();
// processSlowModeTransactionImpl → WithdrawCollateral branch
// validateSender passes, NO requireUnsanctioned, withdrawal succeeds
// Sanctioned attacker receives funds
```

### Citations

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L374-376)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
```

**File:** core/contracts/Endpoint.sol (L196-228)
```text
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

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
