### Title
Linked Signer Can Unilaterally Overwrite Its Own Authorization, Hijacking or Revoking Subaccount Delegation — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction in the sequencer path is validated with `allowLinkedSigner = true`, meaning the **currently linked signer** (an approved entity) can sign a new `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any arbitrary address — including `address(0)` (revocation) or an attacker-controlled address (hijacking) — without the subaccount owner's knowledge or consent.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes the current linked signer as the accepted co-signer: [2](#0-1) 

This means the signature check for a `LinkSigner` transaction accepts a valid signature from **either** the subaccount owner **or** the currently registered linked signer. After the check passes, the mapping is unconditionally overwritten: [3](#0-2) 

The `LinkSigner` struct confirms the linked signer can freely choose the new `signer` value: [4](#0-3) 

The `linkedSigners` mapping is the sole authorization record for subaccount delegation: [5](#0-4) 

---

### Impact Explanation

The linked signer is Nado's analog of an ERC721 "approved entity." The `linkedSigners` mapping is the analog of `_tokenApprovals`. Just as ERC721's `_transfer` silently clears `_tokenApprovals[tokenId]` without the owner's consent, here the linked signer can silently overwrite `linkedSigners[subaccount]` without the owner's consent.

Two concrete outcomes:

1. **Revocation DoS**: The linked signer sets `signer = address(0)`, revoking its own access and leaving the subaccount unmanaged. Any automated strategy or delegated manager the owner relied on is instantly disabled.

2. **Authorization Hijack**: The linked signer sets `signer = attacker_address`. The attacker now holds full linked-signer privileges — they can sign `WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, and order-matching transactions on behalf of the victim's subaccount, draining collateral or manipulating positions.

The `WithdrawCollateral` and `TransferQuote` paths both accept `allowLinkedSigner = true`: [6](#0-5) [7](#0-6) 

So a hijacked linked signer slot directly enables collateral theft.

---

### Likelihood Explanation

Medium. The trigger requires the current linked signer to act maliciously or be compromised. However:

- Linked signers are commonly API keys or hot wallets for trading bots — high-value targets for key compromise.
- A rogue or compromised linked signer has a clear, single-step path to either lock out the owner or redirect all signing authority to an attacker.
- No on-chain mechanism notifies or protects the subaccount owner once the overwrite occurs.
- The slow-mode path for `LinkSigner` correctly restricts to the subaccount owner via `validateSender`, confirming the sequencer path's `allowLinkedSigner = true` is an inconsistency: [8](#0-7) 

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` branch in `processTransactionImpl`:

```solidity
// EndpointTx.sol ~line 581
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only the subaccount owner may change the linked signer
);
```

This mirrors the slow-mode path's behavior, which already enforces that only the subaccount owner (`validateSender`) can submit a `LinkSigner` transaction. [9](#0-8) 

---

### Proof of Concept

1. Alice owns subaccount `0xAlice000...000default`.
2. Alice links Bob (`0xBob`) as her signer via a `LinkSigner` transaction. `linkedSigners[0xAlice000...000default] = 0xBob`.
3. Bob (compromised or malicious) constructs a `SignedLinkSigner` with `sender = 0xAlice000...000default`, `signer = 0xAttacker`, `nonce = currentNonce`, signed by Bob's key.
4. The sequencer includes this transaction. `validateSignedTx(..., true)` accepts Bob's signature because `getLinkedSigner(0xAlice000...000default) == 0xBob`.
5. `linkedSigners[0xAlice000...000default]` is overwritten to `0xAttacker`.
6. The attacker signs a `WithdrawCollateral` transaction for Alice's subaccount. `validateSignedTx(..., true)` accepts the attacker's signature. Alice's collateral is drained. [1](#0-0)

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

**File:** core/contracts/interfaces/IEndpoint.sol (L176-185)
```text
    struct LinkSigner {
        bytes32 sender;
        bytes32 signer;
        uint64 nonce;
    }

    struct SignedLinkSigner {
        LinkSigner tx;
        bytes signature;
    }
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
