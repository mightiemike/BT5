The key code is already found. Let me verify there are no additional guards on the `LinkSigner` path.

### Title
Linked Signer Can Hijack Its Own Delegation by Signing `LinkSigner` Transactions — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` branch in `processTransactionImpl` calls `validateSignedTx` with `allowLinkedSigner=true`. Because `validateSignature` accepts either the subaccount owner **or** the current linked signer, the linked signer itself can sign a `LinkSigner` transaction and overwrite `linkedSigners[subaccount]` with any address it chooses — without the subaccount owner's knowledge or consent.

---

### Finding Description

In `EndpointTx.sol`, the `LinkSigner` transaction type is handled as follows:

```solidity
// EndpointTx.sol lines 576–590
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(...);
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true          // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner=true` passes `getLinkedSigner(sender)` into `verifier.validateSignature`:

```solidity
// Verifier.sol lines 297–303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),   // <-- linked signer accepted
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

There is no additional guard after signature validation that restricts `LinkSigner` mutations to the subaccount owner only. The three checks inside `validateSignedTx` are nonce, signature, and `requireSubaccount` — none of which enforce owner-only authorization for this sensitive operation. [3](#0-2) 

---

### Impact Explanation

**Broken invariant:** `linkedSigners[subaccount]` should only be writable by `address(bytes20(subaccount))` (the subaccount owner). Instead, the current linked signer can overwrite it with any address.

**Concrete state mutation:**
- Pre-state: `linkedSigners[victimSubaccount] = attackerAddress`
- Attacker signs `LinkSigner{sender: victimSubaccount, signer: newAttackerAddress, nonce: N}` with `attackerAddress`
- Post-state: `linkedSigners[victimSubaccount] = newAttackerAddress`

The attacker can rotate the linked signer to a fresh key at will, without the owner's consent. This is an unauthorized modification of the delegation relationship.

**Partial mitigation note:** The original owner can still sign transactions directly (since `recovered == address(uint160(bytes20(sender)))` is always accepted), and can issue their own `LinkSigner` to revoke. However, the attacker can keep rotating to new keys, and in a sequencer-ordered system, the attacker can submit rotation transactions that the sequencer processes before the owner's revocation — especially if the attacker controls or influences submission timing. More critically, the linked signer was granted a scoped delegation (e.g., for trading), not the authority to modify the delegation itself. This is an unauthorized escalation of privilege within the intended trust model.

---

### Likelihood Explanation

The precondition — `linkedSigners[victimSubaccount] == attackerAddress` — is a normal operational state. Users routinely set linked signers for API trading. Any such linked signer can immediately exploit this without any additional privileges. The exploit requires only a valid signed transaction submitted through the normal sequencer path (`submitTransactionsChecked` → `processTransactionImpl`).

---

### Recommendation

Pass `allowLinkedSigner=false` for `LinkSigner` transactions, so that only the subaccount owner (`address(bytes20(sender))`) can authorize changes to the linked signer relationship:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // owner-only: linked signer must not be able to re-link
);
``` [4](#0-3) 

The same fix should be applied to the slow-mode `LinkSigner` path, which already enforces `validateSender(txn.sender, sender)` (requiring the on-chain `msg.sender` to match the subaccount owner), so it is not affected by this issue. [5](#0-4) 

---

### Proof of Concept

```solidity
// Setup
linkedSigners[victimSubaccount] = attackerAddress;

// Attacker constructs and signs
IEndpoint.LinkSigner memory lsTx = IEndpoint.LinkSigner({
    sender: victimSubaccount,
    signer: bytes32(uint256(uint160(newAttackerAddress))),
    nonce: nonces[address(uint160(bytes20(victimSubaccount)))]
});
bytes memory sig = sign(attackerPrivKey, EIP712digest(lsTx));

// Submit through sequencer
endpoint.submitTransactionsChecked(..., abi.encodePacked(
    uint8(IEndpoint.TransactionType.LinkSigner),
    abi.encode(IEndpoint.SignedLinkSigner({tx: lsTx, signature: sig}))
));

// Assert
assert(linkedSigners[victimSubaccount] == newAttackerAddress);
// Original owner's linked-signer-based authorization is now gone;
// attacker controls the linked signer slot indefinitely.
```

### Citations

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
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
