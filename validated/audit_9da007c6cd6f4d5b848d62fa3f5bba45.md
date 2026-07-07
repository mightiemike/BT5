### Title
Linked Signer Can Self-Authorize `LinkSigner` Transactions, Enabling Permanent Subaccount Signing Takeover - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **current linked signer** — a delegated trading authority — can sign and submit a `LinkSigner` transaction that replaces itself with any attacker-controlled address. The wrong entity (a delegated signer) is permitted to authorize an account-management operation that modifies the authorization structure itself.

---

### Finding Description

The `processTransactionImpl` function handles `LinkSigner` transactions as follows:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
        transaction[1:],
        (IEndpoint.SignedLinkSigner)
    );
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` calls `validateSignature`, which passes the current linked signer as an accepted signer when `allowLinkedSigner = true`:

```solidity
function validateSignature(...) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` then accepts a signature from either the subaccount owner **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the linked signer can sign a `LinkSigner` transaction pointing to any `signer` address it chooses, and the protocol will accept it and overwrite `linkedSigners[subaccount]`.

**The slow-mode path correctly restricts this.** In `processSlowModeTransactionImpl`, `LinkSigner` uses `validateSender`, which requires `msg.sender == address(uint160(bytes20(txn.sender)))` — i.e., only the actual subaccount owner address can submit it:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(...);
    validateSender(txn.sender, sender);   // ← enforces owner-only
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The fast path and slow path are inconsistent: the slow path correctly restricts `LinkSigner` to the subaccount owner, but the fast path allows the linked signer to authorize it.

The protocol already demonstrates awareness of this distinction. For `WithdrawCollateralV2`, `allowLinkedSigner` is conditionally set to `false` when `sendTo != address(0)`, showing the designers intentionally restrict linked signer permissions for sensitive operations:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    signedTx.tx.sendTo == address(0)   // ← conditional restriction
);
``` [5](#0-4) 

No equivalent restriction exists for `LinkSigner`.

---

### Impact Explanation

A malicious or compromised linked signer can:

1. Sign a `LinkSigner` transaction with `signer = attacker_address` and submit it through the sequencer.
2. The protocol overwrites `linkedSigners[subaccount] = attacker_address`.
3. The attacker now holds linked signer privileges: they can sign `WithdrawCollateral` and `TransferQuote` transactions (both processed with `allowLinkedSigner = true`) to drain the subaccount.
4. The legitimate subaccount owner can only recover via slow mode, which has a hardcoded 3-day delay (`SLOW_MODE_TX_DELAY`). [6](#0-5) 

During the 3-day window, the attacker retains full linked signer authority and can drain all collateral. The `linkedSigners` mapping is the corrupted state: [7](#0-6) 

---

### Likelihood Explanation

Linked signers are commonly trading bots or automated systems. A compromised bot, a malicious third-party signer service, or an insider at a linked signer operator can execute this attack with no special privileges — only a valid EIP-712 signature over a `LinkSigner` struct is required. The sequencer processes any validly signed transaction, so no sequencer collusion is needed.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

Only the address embedded in the subaccount bytes32 (the owner) should be permitted to change the linked signer. This aligns the fast path with the slow path, which already enforces this restriction via `validateSender`.

---

### Proof of Concept

1. Alice sets `linkedSigners[alice_subaccount] = bot_address` via a normal `LinkSigner` transaction.
2. The bot (or an attacker who compromised it) constructs: `LinkSigner { sender: alice_subaccount, signer: attacker_address, nonce: current_nonce }`.
3. The bot signs the EIP-712 digest and submits the transaction to the sequencer.
4. `processTransactionImpl` calls `validateSignedTx(..., true)`. `getLinkedSigner(alice_subaccount)` returns `bot_address`. The bot's signature is valid. The check passes.
5. `linkedSigners[alice_subaccount] = attacker_address` is written.
6. The attacker signs `WithdrawCollateral { sender: alice_subaccount, ... }` and submits it. It passes `validateSignedTx(..., true)` because `getLinkedSigner` now returns `attacker_address`.
7. Alice's collateral is drained. Alice can only recover via slow mode after 3 days.

### Citations

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L442-448)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
```

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/Verifier.sol (L297-303)
```text
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/Endpoint.sol (L152-153)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
