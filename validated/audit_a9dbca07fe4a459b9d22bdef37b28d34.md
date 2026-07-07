### Title
Linked Signer Can Overwrite Its Own Authorization — (`File: core/contracts/EndpointTx.sol`)

### Summary

The `LinkSigner` transaction in the sequencer path is validated with `allowLinkedSigner = true`, meaning the current linked signer can sign and submit a `LinkSigner` transaction to replace the linked signer mapping for any subaccount it controls. The slow-mode path for the same transaction type correctly restricts execution to the subaccount owner only. This mismatch allows a linked signer to hijack the `linkedSigners` slot for a subaccount without the owner's consent.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is handled as follows:

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
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` passes the current linked signer address to `verifier.validateSignature` as an accepted signer:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

The verifier accepts a signature from either the subaccount owner address or the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the current linked signer can produce a valid signature for a `LinkSigner` transaction and have the sequencer process it, overwriting `linkedSigners[subaccount]` with any address of their choosing.

The slow-mode path for the same `LinkSigner` transaction type uses `validateSender`, which enforces that only the address embedded in the subaccount bytes32 (i.e., the owner) can submit it:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.LinkSigner)
    );
    validateSender(txn.sender, sender);   // ← owner-only check
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
``` [4](#0-3) 

`validateSender` requires `address(uint160(bytes20(txSender))) == sender`, i.e., the transaction must originate from the subaccount owner address: [5](#0-4) 

The two paths are inconsistent. The slow-mode path correctly restricts `LinkSigner` to the owner; the sequencer path does not.

---

### Impact Explanation

A linked signer can overwrite `linkedSigners[subaccount]` with any address. Concrete consequences:

1. **Backdoor persistence**: A compromised linked signer (e.g., a hot wallet that was stolen) can replace itself with a fresh attacker-controlled address before the owner can revoke it, maintaining persistent signing authority over the subaccount.
2. **Denial of revocation**: The attacker can keep re-setting the linked signer faster than the owner can clear it, since both parties can submit `LinkSigner` transactions through the sequencer.
3. **Unauthorized signer installation**: Any linked signer (e.g., a trading bot) can unilaterally install a different address as the linked signer without the owner's knowledge or consent.

The corrupted state is `linkedSigners[subaccount]`, which controls who can sign withdrawals, trades, and other sensitive operations on behalf of the subaccount. [6](#0-5) 

---

### Likelihood Explanation

Any address that has been set as a linked signer for a subaccount can trigger this. Linked signers are commonly used for automated trading bots and hot wallets. A compromised or malicious linked signer can exploit this immediately through the normal sequencer submission path with no special privileges beyond already being a linked signer.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`, consistent with the slow-mode path which already enforces owner-only access:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // LinkSigner must be signed by the subaccount owner only
);
``` [7](#0-6) 

---

### Proof of Concept

1. Alice owns subaccount `0xAlice000000000000000000000000000000000000` and has set `linkedSigners[subaccount] = BotAddress`.
2. `BotAddress` is compromised by an attacker.
3. Attacker uses `BotAddress`'s private key to sign a `SignedLinkSigner` transaction where `signedTx.tx.sender = Alice's subaccount` and `signedTx.tx.signer = AttackerAddress`.
4. Attacker submits this transaction to the sequencer.
5. The sequencer calls `processTransactionImpl`. `validateSignedTx(..., true)` passes because `BotAddress == getLinkedSigner(Alice's subaccount)`.
6. `linkedSigners[Alice's subaccount]` is overwritten with `AttackerAddress`.
7. Alice attempts to revoke by submitting her own `LinkSigner` transaction. Attacker races to re-set it using `AttackerAddress` (now the new linked signer), which also passes `validateSignedTx(..., true)`.
8. Attacker maintains persistent control over the linked signer slot.

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

**File:** core/contracts/EndpointTx.sol (L177-184)
```text
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
