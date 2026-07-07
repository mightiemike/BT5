### Title
Linked Signer Can Overwrite Itself via `LinkSigner` Fast Path, Enabling Persistent Unauthorized Subaccount Access - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction is validated with `allowLinkedSigner = true`, meaning the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to replace itself with any arbitrary address. This is structurally inconsistent with the slow-mode path, which enforces `validateSender` (requiring the actual subaccount owner). A compromised linked signer can silently install an attacker-controlled replacement, maintaining persistent unauthorized access even after the user believes they have revoked the original key.

---

### Finding Description

**Vulnerability class:** Unauthorized subaccount mutation via missing origin/identity check — direct analog to the IBC middleware sender-check bypass.

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted alternate signer: [2](#0-1) 

`Verifier.validateSignature` then accepts a signature from **either** the subaccount owner **or** the linked signer: [3](#0-2) 

After validation, the linked signer is unconditionally overwritten: [4](#0-3) 

**The inconsistency is the root cause.** The slow-mode path for the same `LinkSigner` transaction type uses `validateSender(txn.sender, sender)`, which requires the actual `msg.sender` to match the address embedded in the subaccount — i.e., only the subaccount owner: [5](#0-4) 

The slow-mode path enforces owner-only authorization. The fast path does not. This is the exact structural analog to the IBC middleware bug: a check that should gate processing is absent in one code path, allowing an unintended actor (the linked signer) to perform an action reserved for the owner.

---

### Impact Explanation

The concrete state delta: `linkedSigners[subaccount]` is overwritten with an attacker-controlled address.

Attack chain:
1. User A has subaccount `0xUserA...` with linked signer `0xHotWallet` (e.g., an API key or trading bot key).
2. Attacker compromises `0xHotWallet`.
3. Attacker uses `0xHotWallet` to sign a sequencer-submitted `LinkSigner` transaction setting `linkedSigners[0xUserA...] = 0xAttacker`.
4. `processTransactionImpl` accepts the signature because `allowLinkedSigner = true` and `0xHotWallet == getLinkedSigner(0xUserA...)`.
5. User A notices suspicious activity and submits a slow-mode `LinkSigner` to revoke `0xHotWallet`.
6. But `linkedSigners[0xUserA...]` is now `0xAttacker`, not `0xHotWallet`. The user's revocation targets the wrong key.
7. Attacker retains full linked-signer privileges via `0xAttacker` and can drain the subaccount (withdraw collateral, transfer quote) before the user discovers the substitution.

The 3-day slow-mode delay (`SLOW_MODE_TX_DELAY`) means the user cannot quickly correct the state once the substitution is made. The attacker has a window to drain all collateral using the new linked signer, which has the same full privileges as the original. [6](#0-5) 

---

### Likelihood Explanation

Linked signers are the standard mechanism for delegating trading authority to hot wallets, bots, or API keys. Key compromise of a hot wallet is a realistic and common threat. The attacker's entry path is fully unprivileged: they only need the compromised linked signer's private key, which is already a realistic attack surface. No admin access, sequencer compromise, or governance capture is required. The sequencer will include the attacker's `LinkSigner` transaction in a normal batch submission.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner should be permitted to update the linked signer, consistent with the slow-mode path:

```solidity
// EndpointTx.sol - processTransactionImpl
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
        false  // <-- Only the subaccount owner may update the linked signer
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
```

---

### Proof of Concept

```solidity
// Scenario: attacker has compromised the linked signer key

// Step 1: attacker constructs a LinkSigner tx targeting victim's subaccount
IEndpoint.LinkSigner memory innerTx = IEndpoint.LinkSigner({
    sender: victimSubaccount,   // e.g. bytes32(abi.encodePacked(victimAddr, bytes12("default")))
    signer: bytes32(uint256(uint160(attackerAddr))),
    nonce: currentNonce
});

IEndpoint.SignedLinkSigner memory signedTx = IEndpoint.SignedLinkSigner({
    tx: innerTx,
    signature: sign(innerTx, compromisedLinkedSignerKey)  // signed by the LINKED SIGNER, not owner
});

// Step 2: sequencer includes this in a batch (attacker submits via normal API)
// endpoint.submitTransactionsChecked(...) → processTransaction → processTransactionImpl

// Step 3: validateSignedTx(..., true) accepts the linked signer's signature
// linkedSigners[victimSubaccount] = attackerAddr  ← state corrupted

// Step 4: victim revokes "0xHotWallet" via slow mode — but linkedSigner is now attackerAddr
// attacker retains full access and drains collateral via WithdrawCollateral signed by attackerAddr
```

The attacker-controlled entry path is: compromised linked signer key → sequencer API submission → `submitTransactionsChecked` → `processTransactionImpl` → `LinkSigner` branch with `allowLinkedSigner = true` → `linkedSigners[victim]` overwritten. [1](#0-0) [7](#0-6)

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

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```

**File:** core/contracts/Endpoint.sol (L376-380)
```text

    function getEndpointTx() external view returns (address) {
        return endpointTx;
    }

```
