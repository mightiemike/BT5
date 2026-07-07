### Title
Linked Signer Can Overwrite Its Own Authorization Entry, Hijacking Subaccount Signing Privileges - (File: core/contracts/EndpointTx.sol)

---

### Summary

The signed `LinkSigner` transaction is processed with `allowLinkedSigner = true`, meaning the currently-registered linked signer can sign a `LinkSigner` transaction to replace itself with any arbitrary address. This is the Nado analog of the VotingEscrow `split()` bug: an authorized-but-not-owner actor mutates a critical ownership/authorization state variable in a way that should be restricted to the actual subaccount owner.

---

### Finding Description

In `EndpointTx.sol`, the fast-path handler for `TransactionType.LinkSigner` calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← allowLinkedSigner
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
```

`validateSignedTx` with `allowLinkedSigner = true` passes `getLinkedSigner(sender)` as the permitted alternate signer to `verifier.validateCompactSignature`: [2](#0-1) 

The verifier accepts a signature from either the subaccount owner address or the registered linked signer: [3](#0-2) 

Therefore, the currently-registered linked signer can craft and submit a valid `SignedLinkSigner` transaction that writes any address into `linkedSigners[subaccount]`, including:
- A new address the attacker controls (persistent access escalation)
- `address(0)` (revocation of the owner's linked signer, griefing)

The `LinkSigner` EIP-712 type string confirms `signer` is a free field in the signed payload: [4](#0-3) 

By contrast, the slow-mode `LinkSigner` path validates `txn.sender == msg.sender` (the on-chain caller must be the owner), so the protection exists for slow-mode but is absent for the fast-path signed variant. [5](#0-4) 

---

### Impact Explanation

**Impact: Medium.**

A malicious or compromised linked signer can overwrite `linkedSigners[subaccount]` to any address without the subaccount owner's consent. The new linked signer inherits all signing privileges that `allowLinkedSigner = true` grants, including:

- Signing `WithdrawCollateral` (V1) to drain the subaccount's collateral to the owner's wallet address (forced withdrawal, not direct theft, but disruptive)
- Signing further `LinkSigner` transactions to maintain persistent access
- Signing `TransferQuote` between same-owner subaccounts

Direct fund theft to an attacker-controlled address is blocked because `WithdrawCollateral` V1 always sends to `address(uint160(bytes20(sender)))` (the owner): [6](#0-5) 

And `WithdrawCollateralV2` with a custom `sendTo` disables `allowLinkedSigner`: [7](#0-6) 

The concrete broken invariant is: **the `linkedSigners` mapping for a subaccount should only be writable by the subaccount owner, not by the linked signer itself.** A linked signer can permanently replace itself with an attacker-controlled address, surviving any attempt by the original linked signer to "expire" or rotate out.

---

### Likelihood Explanation

**Likelihood: Medium.**

The trigger requires an existing linked signer to act maliciously or be compromised. Linked signers are commonly used in Nado for programmatic/automated trading (bots, market makers). A compromised bot key or a malicious third-party integration that was granted linked signer access can execute this attack without any further on-chain preconditions. The owner can override by signing their own `LinkSigner` transaction directly, but only if they detect the attack.

---

### Recommendation

Process `LinkSigner` with `allowLinkedSigner = false` so that only the subaccount owner (the address embedded in the first 20 bytes of the `sender` subaccount identifier) can authorize changes to the linked signer mapping:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only owner may change linked signer
);
```

This is consistent with the slow-mode path, which already enforces that only the on-chain `msg.sender` (the owner) can submit a `LinkSigner` transaction. [5](#0-4) 

---

### Proof of Concept

1. Owner Alice deploys subaccount `alice_subaccount` and registers Bob's key as linked signer via slow-mode `LinkSigner`.
2. Bob (linked signer) constructs a `SignedLinkSigner` payload: `{ sender: alice_subaccount, signer: charlie_address, nonce: N }`.
3. Bob signs the EIP-712 digest with his own key.
4. Bob submits the signed transaction to the Endpoint fast path.
5. `validateSignedTx(..., allowLinkedSigner=true)` passes because Bob is the registered linked signer for `alice_subaccount`.
6. `linkedSigners[alice_subaccount]` is overwritten with `charlie_address`.
7. Charlie now has full linked signer privileges on Alice's subaccount. Bob's key is no longer needed.
8. Charlie can sign `WithdrawCollateral` to force-drain Alice's collateral to Alice's wallet, or sign further `LinkSigner` transactions to maintain persistent control. [1](#0-0)

### Citations

**File:** core/contracts/EndpointTx.sol (L186-198)
```text
    function validateCompactSignature(
        bytes32 sender,
        bytes32 digest,
        IEndpoint.CompactSignature memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateCompactSignature(
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

**File:** core/contracts/Verifier.sol (L30-31)
```text
    string internal constant LINK_SIGNER_SIGNATURE =
        "LinkSigner(bytes32 sender,bytes32 signer,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L306-319)
```text
    function validateCompactSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        IEndpoint.CompactSignature memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature.r, signature.vs);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L404-406)
```text
        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }
```
