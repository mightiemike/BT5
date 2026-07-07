### Title
Linked Signer Can Self-Replace via `LinkSigner` with `allowLinkedSigner = true`, Enabling Full Subaccount Takeover — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **existing linked signer** — not just the subaccount owner — can authorize a `LinkSigner` transaction that replaces itself with any arbitrary address. The signature check appears to enforce owner-level authorization over who controls the subaccount's signing authority, but it does not: a compromised session key can silently escalate to full subaccount control and drain funds via `TransferQuote`.

---

### Finding Description

In the fast-path sequencer pipeline, `processTransactionImpl` handles `LinkSigner` as follows:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner = true
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes the current linked signer to `Verifier.validateSignature`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` accepts a signature from **either** the subaccount owner address **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Therefore, the existing linked signer can sign a `LinkSigner` transaction that writes any new address into `linkedSigners[subaccount]`:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [4](#0-3) 

This is structurally identical to the external report's bug class: a security check (signature validation) appears to enforce owner-level authorization over a sensitive state mutation, but it actually accepts the existing delegated key's signature, providing no guarantee that the **owner** authorized the change.

**Contrast with the slow-mode path:** In `processSlowModeTransactionImpl`, the same `LinkSigner` type is gated by `validateSender(txn.sender, sender)`, which enforces `msg.sender == address(uint160(bytes20(txn.sender)))` — i.e., the on-chain caller must be the subaccount owner. The fast path has no equivalent owner-only gate. [5](#0-4) 

---

### Impact Explanation

Once the attacker's address is installed as the linked signer, they can sign `TransferQuote` transactions (also validated with `allowLinkedSigner = true`) to transfer the victim's quote balance to any attacker-controlled subaccount:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner = true
);
clearinghouse.transferQuote(signedTx.tx);
``` [6](#0-5) 

The `recipient` field in `TransferQuote` is unconstrained — it can be any subaccount, including one the attacker controls. The `_recordSubaccount` call on line 598 even auto-registers a fresh attacker subaccount if needed.

**Corrupted state delta:** `linkedSigners[victim_subaccount]` is overwritten to an attacker address; subsequently the victim's quote balance in `SpotEngine` is drained to the attacker's subaccount via `clearinghouse.transferQuote`.

---

### Likelihood Explanation

Linked signers are session keys — hot keys used for automated order submission, often stored in servers or bots. They are materially more likely to be compromised than the owner's cold wallet. The attack requires only that the linked signer key be leaked or stolen; no admin access, governance capture, or sequencer compromise is needed. The attacker submits two sequencer transactions (one `LinkSigner`, one `TransferQuote`) that are individually valid and indistinguishable from legitimate traffic.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Changing the linked signer is a privileged ownership action and must require the subaccount owner's signature, not the existing session key's signature. This aligns the fast path with the slow-mode path, which already enforces `validateSender` (owner-only).

```solidity
// Before (vulnerable):
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);

// After (fixed):
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, false);
``` [7](#0-6) 

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and has set `linkedSigners[alice_subaccount] = bob_key` (a session key for automated trading).
2. Attacker obtains `bob_key` (e.g., via server compromise).
3. Attacker crafts a `SignedLinkSigner` transaction: `sender = alice_subaccount`, `signer = attacker_address`, signed with `bob_key`. The EIP-712 digest is computed over `(alice_subaccount, attacker_address, current_nonce)`.
4. Attacker submits this to the sequencer. On-chain, `validateSignedTx(..., true)` recovers `bob_key`, which equals `linkedSigners[alice_subaccount]` — validation passes. `linkedSigners[alice_subaccount]` is overwritten to `attacker_address`.
5. Attacker crafts a `SignedTransferQuote` transaction: `sender = alice_subaccount`, `recipient = attacker_subaccount`, `amount = full_balance`, signed with `attacker_key`. Validation passes because `linkedSigners[alice_subaccount] == attacker_address`.
6. `clearinghouse.transferQuote` moves Alice's entire quote balance to `attacker_subaccount`. Alice's collateral is fully drained. [1](#0-0) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L593-614)
```text
        } else if (txType == IEndpoint.TransactionType.TransferQuote) {
            IEndpoint.SignedTransferQuote memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedTransferQuote)
            );
            _recordSubaccount(signedTx.tx.recipient);
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            if (
                RiskHelper.isIsolatedSubaccount(signedTx.tx.recipient) ||
                RiskHelper.isIsolatedSubaccount(signedTx.tx.sender)
            ) {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE / 10);
            } else {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            }
            clearinghouse.transferQuote(signedTx.tx);
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
