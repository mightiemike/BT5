### Title
Slow-Mode `WithdrawCollateral` Skips Nonce Validation, Enabling Double Withdrawal — (`core/contracts/EndpointTx.sol`)

---

### Summary

`processSlowModeTransactionImpl` handles `WithdrawCollateral` using only `validateSender`, never calling `validateNonce`. Because `submitSlowModeTransaction` is open to any user, an attacker can queue a slow-mode withdrawal for their own subaccount and simultaneously have the sequencer process a signed fast-path withdrawal for the same amount. Both paths call `clearinghouse.withdrawCollateral` independently, with no shared replay guard, resulting in two full withdrawals from a single balance.

---

### Finding Description

**Fast path** (`processTransactionImpl` → `WithdrawCollateral`):

Decodes the payload as `SignedWithdrawCollateral` and calls `validateSignedTx`, which internally calls `validateNonce` (incrementing `nonces[address]`) before executing the withdrawal. [1](#0-0) 

**Slow path** (`processSlowModeTransactionImpl` → `WithdrawCollateral`):

Decodes the payload as the unsigned `WithdrawCollateral` struct. The only guard is `validateSender`, which checks that `address(uint160(bytes20(txn.sender))) == sender` — i.e., the caller owns the subaccount. The `nonce` field present in the struct is decoded but **never validated or consumed**. [2](#0-1) 

The `WithdrawCollateral` struct carries a `nonce` field, but the slow path ignores it entirely: [3](#0-2) 

`validateNonce` is defined as: [4](#0-3) 

`validateSender` (the only check in the slow path) is: [5](#0-4) 

**Queuing is permissionless.** `submitSlowModeTransaction` is `external` with no caller restriction for `WithdrawCollateral` — it falls into the `else` branch that only charges a slow-mode fee: [6](#0-5) 

**Execution after timeout is also permissionless.** `executeSlowModeTransaction` has no access control: [7](#0-6) 

---

### Impact Explanation

An attacker with collateral balance `X` can extract `2X`:

1. Fast-path withdrawal executes, consuming nonce N and transferring `X` out.
2. Slow-path withdrawal executes (no nonce consumed), transferring another `X` out.

The protocol's engine balance is debited twice for a single user balance, directly draining protocol-held collateral. This is a Critical asset-loss impact.

---

### Likelihood Explanation

The attack requires no special privileges. Any user who owns a subaccount can:
- Call `submitSlowModeTransaction` themselves (paying only the slow-mode fee).
- Submit a signed withdrawal to the sequencer through the normal off-chain flow.

The sequencer has no mechanism to detect or block the concurrent slow-mode queue entry. The 3-day delay is the only friction, and it is not a security control — it is a user-protection delay that the attacker simply waits out.

---

### Recommendation

Add `validateNonce` to the `WithdrawCollateral` branch of `processSlowModeTransactionImpl`, mirroring the fast path:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
    validateNonce(txn.sender, txn.nonce);   // ADD THIS
    clearinghouse.withdrawCollateral(...);
}
```

The `nonce` field already exists in the `WithdrawCollateral` struct — it is simply never consumed in the slow path. Consuming it here closes the replay window. The comment at line 200 (`// TODO: these do not need senders or nonces`) is incorrect for `WithdrawCollateral` and should be removed. [8](#0-7) 

---

### Proof of Concept

```solidity
// Hardhat test (chainId 31337 — silent-revert protection is disabled per line 201 of Endpoint.sol)

// 1. Attacker queues slow-mode withdrawal BEFORE or AFTER signing the fast-path one
bytes memory slowPayload = abi.encodePacked(
    uint8(IEndpoint.TransactionType.WithdrawCollateral),
    abi.encode(IEndpoint.WithdrawCollateral({
        sender: attackerSubaccount,
        productId: QUOTE_PRODUCT_ID,
        amount: WITHDRAW_AMOUNT,
        nonce: 0  // ignored by slow path
    }))
);
endpoint.submitSlowModeTransaction(slowPayload);  // pays slow mode fee, queued

// 2. Attacker submits signed WithdrawCollateral to sequencer (off-chain)
//    Sequencer calls submitTransactionsChecked → processTransactionImpl
//    → validateNonce(nonce=0) passes, nonces[attacker]++ → 1
//    → clearinghouse.withdrawCollateral executes: first withdrawal ✓

// 3. After SLOW_MODE_TX_DELAY (3 days), anyone calls:
endpoint.executeSlowModeTransaction();
//    → processSlowModeTransactionImpl → validateSender passes (attacker is caller)
//    → NO validateNonce call
//    → clearinghouse.withdrawCollateral executes: second withdrawal ✓

// Assert: attacker received 2 * WITHDRAW_AMOUNT
```

### Citations

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L200-202)
```text
    // TODO: these do not need senders or nonces
    // we can save some gas by creating new structs
    function processSlowModeTransactionImpl(
```

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

**File:** core/contracts/EndpointTx.sol (L369-384)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

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

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L80-85)
```text
    struct WithdrawCollateral {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
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
