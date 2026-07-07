### Title
Linked Signer Can Self-Overwrite via `LinkSigner` Transaction, Enabling Persistent Unauthorized Subaccount Control — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-mode transaction path in `EndpointTx.sol` is validated with `allowLinkedSigner = true`, meaning the **current linked signer** — an unprivileged external party — can sign a new `LinkSigner` transaction to replace itself with any attacker-controlled address. This allows a compromised linked signer to front-run the subaccount owner's revocation attempt, permanently locking the owner out and maintaining unauthorized signing authority over the subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes the current linked signer as the accepted alternate signer: [2](#0-1) 

`Verifier.validateSignature` then accepts the signature if it comes from either the subaccount owner address **or** the linked signer: [3](#0-2) 

This means the current linked signer can produce a valid `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address it chooses: [4](#0-3) 

The nonce is keyed to the owner's address and is shared across all transaction types: [5](#0-4) 

This means a linked signer's `LinkSigner` transaction consumes the owner's nonce, causing the owner's concurrent revocation transaction (signed with the same nonce) to fail with `ERR_WRONG_NONCE`.

By contrast, the **slow-mode** `LinkSigner` path correctly uses `validateSender`, which requires `msg.sender` to equal the subaccount owner's address — the linked signer cannot exploit this path: [6](#0-5) 

The asymmetry between the two paths confirms the fast-mode behavior is a design flaw, not an intentional feature.

---

### Impact Explanation

**Impact: High.**

Once the linked signer overwrites itself with an attacker-controlled address C, C holds persistent signing authority over the victim's subaccount. C can:

- Sign `WithdrawCollateral` (V1) transactions — funds are sent to the subaccount owner's address, so direct theft via withdrawal is not possible through this path alone.
- Sign `MatchOrders` / `LiquidateSubaccount` transactions, enabling manipulation of the account's positions through the offchain exchange at unfavorable prices, draining value to a counterparty.
- Sign `MintNlp` / `BurnNlp` transactions, manipulating the account's NLP exposure.
- Continuously front-run every subsequent revocation attempt by the owner (each time consuming the owner's nonce), making revocation practically impossible without out-of-band coordination with the sequencer.

The corrupted state is `linkedSigners[subaccount]` — a critical access-control mapping that governs who can authorize all sensitive operations on the subaccount. [7](#0-6) 

---

### Likelihood Explanation

**Likelihood: Low.**

Exploitation requires the linked signer's private key to be compromised or the linked signer to be a malicious party. However, linked signers are a standard, explicitly supported protocol feature (e.g., API keys for trading bots), and the protocol provides no protection against this self-overwrite. The attack requires no special privileges beyond what the linked signer already holds.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address encoded in the first 20 bytes of the `sender` bytes32) should be permitted to change the linked signer. This is consistent with the slow-mode path, which already enforces this restriction via `validateSender`.

```solidity
// In EndpointTx.processTransactionImpl, LinkSigner branch:
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // was: true — linked signer must NOT be able to change itself
);
```

---

### Proof of Concept

1. User A owns subaccount `S` and links signer B (e.g., a trading bot key). `linkedSigners[S] = B`.
2. User A decides to revoke B. A signs a `LinkSigner` transaction with `signer = address(0)` at nonce N and submits it to the sequencer.
3. B (compromised) observes the pending revocation in the sequencer's mempool or submission queue. B signs a `LinkSigner` transaction with `signer = attacker_C` at the same nonce N (B's signature is accepted because `allowLinkedSigner = true`).
4. The sequencer includes B's transaction before A's in the same batch. B's transaction executes: `linkedSigners[S] = attacker_C`, nonce increments to N+1.
5. A's revocation transaction executes next: `validateNonce` fails because nonce N+1 ≠ N. A's revocation is silently dropped.
6. `linkedSigners[S] = attacker_C`. Attacker C now has persistent signing authority over subaccount S.
7. C signs `MatchOrders` transactions pairing S against C's own subaccount at prices favorable to C, draining S's collateral through the offchain exchange. [1](#0-0) [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```

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

**File:** core/contracts/Verifier.sol (L291-304)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
