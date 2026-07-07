### Title
Linked Signer Can Unilaterally Replace Itself, Permanently Hijacking Subaccount Signing Authority — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction in `processTransactionImpl` is validated with `allowLinkedSigner = true`, meaning the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to replace itself with any arbitrary address. This allows a compromised linked signer to permanently transfer signing authority to an attacker-controlled address, decoupling the subaccount from its legitimate owner and enabling persistent collateral theft.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
// EndpointTx.sol lines 576–590
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
        true   // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` as the permitted signer:

```solidity
// EndpointTx.sol lines 172–184
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` then accepts a signature from **either** the owner address embedded in the subaccount bytes32 **or** the linked signer:

```solidity
// Verifier.sol lines 291–304
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the current linked signer can submit a valid `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address it chooses — including an attacker-controlled one.

**Contrast with the slow-mode path**: In `processSlowModeTransactionImpl`, the `LinkSigner` case uses `validateSender(txn.sender, sender)`, which enforces that `msg.sender == address(uint160(bytes20(txn.sender)))` — i.e., only the actual owner address can submit this via slow mode. The fast (sequencer-submitted) path has no such restriction. [4](#0-3) 

---

### Impact Explanation

Once the attacker's address is installed as the linked signer, it can:

1. **Drain collateral** via `WithdrawCollateral` (fast path, `allowLinkedSigner = true` unconditionally at line 418–424), directing funds to the `WithdrawPool`.
2. **Place or cancel orders** via `MatchOrders` / `MatchOrdersWithAmount`, which pass `getLinkedSignerOrNlpSigner` to `OffchainExchange` for order validation.
3. **Transfer quote** to other subaccounts via `TransferQuote` (`allowLinkedSigner = true` at line 599–605). [5](#0-4) [6](#0-5) 

The owner can attempt to revoke by submitting a new `LinkSigner` via slow mode (3-day delay) or via the sequencer fast path (owner key required). However, the attacker's linked signer can continuously re-submit `LinkSigner` transactions to re-install itself, creating a persistent race condition that the owner cannot reliably win without the sequencer's cooperation.

**Corrupted state**: `linkedSigners[subaccount]` is set to an attacker-controlled address, permanently replacing the legitimate session key.

---

### Likelihood Explanation

Linked signers are by design session keys — stored in trading bots, browser extensions, or hot wallets — and are materially more likely to be compromised than the cold owner key. Any key compromise (phishing, malware, leaked `.env`, compromised dependency) of the linked signer is sufficient to trigger this attack. No admin access, governance capture, or sequencer compromise is required; the attacker only needs to submit a single sequencer-batched `LinkSigner` transaction signed by the compromised session key.

---

### Recommendation

Change the `LinkSigner` processing in `processTransactionImpl` to use `allowLinkedSigner = false`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This aligns the fast path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner` via `validateSender`. The linked signer is a delegated capability for trading operations; the authority to grant or revoke that delegation must remain exclusively with the subaccount owner. [1](#0-0) 

---

### Proof of Concept

1. Alice owns subaccount `0xAlice000...000` and has linked Bob (`0xBob`) as her session key via a prior `LinkSigner` transaction.
2. Attacker compromises Bob's private key.
3. Attacker constructs a `SignedLinkSigner` transaction:
   - `sender = 0xAlice000...000`
   - `signer = 0xAttacker000...000` (attacker-controlled address, zero-padded to bytes32)
   - `nonce = nonces[0xBob]` (current nonce for Bob's address)
   - Signs the EIP-712 digest with Bob's compromised key.
4. Attacker submits this transaction to the sequencer (or waits for it to be included in a batch via `submitTransactionsChecked`).
5. `processTransactionImpl` validates the signature: `recovered == 0xBob == getLinkedSigner(0xAlice000...000)` → passes.
6. `linkedSigners[0xAlice000...000]` is now set to `0xAttacker000...000`.
7. Attacker immediately submits a `WithdrawCollateral` transaction signed by `0xAttacker000...000`, draining Alice's collateral balance.
8. If Alice attempts to revoke via slow mode (3-day delay), the attacker re-submits step 3–6 using `0xAttacker000...000` to re-install itself before the revocation executes. [1](#0-0) [7](#0-6)

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

**File:** core/contracts/EndpointTx.sol (L418-424)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
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

**File:** core/contracts/EndpointTx.sol (L599-605)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
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
