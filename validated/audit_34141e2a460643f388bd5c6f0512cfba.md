### Title
Linked Signer Can Unilaterally Reassign Its Own Role Without Subaccount Owner Consent — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` transaction handler in `EndpointTx.sol` calls `validateSignedTx` with `allowLinkedSigner = true`, meaning the **current linked signer** — not just the subaccount owner — can sign and submit a `LinkSigner` transaction. This allows a malicious or compromised linked signer to unilaterally reassign signing authority to any arbitrary address, with no acceptance step from the new signer and no recovery mechanism for the subaccount owner.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch decodes a `SignedLinkSigner` and calls:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` routes through `validateSignature`, which passes `getLinkedSigner(sender)` to the verifier:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

The verifier therefore accepts a valid signature from **either** the subaccount owner **or** the current linked signer. Because the `LinkSigner` transaction itself is processed with this flag set to `true`, the current linked signer can sign a `LinkSigner` payload that replaces `linkedSigners[subaccount]` with any address — including one they control or an inaccessible address — without the subaccount owner's knowledge or consent.

The `linkedSigners` mapping is the sole source of truth for delegated signing authority: [3](#0-2) 

The slow-mode `LinkSigner` path correctly restricts the operation to the subaccount owner via `validateSender`, but the fast-mode path does not enforce this restriction: [4](#0-3) 

This is the direct analog to the BridgeRoles inconsistency: the entity holding the delegated role (`linkedSigner` ≈ `btcBridge`) can transfer that role unilaterally, just as `transferBtcBridge` was callable by `onlyBtcBridge` rather than `onlySuperAdmin`.

---

### Impact Explanation

A malicious or compromised linked signer can:

1. **Silently reassign signing authority** to a new address they control, maintaining persistent access to the subaccount even after the owner believes they have revoked it by setting a new linked signer.
2. **Set the linked signer to an inaccessible address**, disabling the linked signer slot and forcing the subaccount owner to submit a slow-mode transaction to recover.

The linked signer can sign withdrawal (`WithdrawCollateral`), liquidation (`LiquidateSubaccount`), and quote transfer (`TransferQuote`) transactions on behalf of the subaccount. Unauthorized reassignment of this role directly threatens subaccount assets. [5](#0-4) 

---

### Likelihood Explanation

Linked signers are a standard feature used by traders for automated bots and delegated operations. Any linked signer that becomes malicious or is compromised (e.g., a hot wallet key leak) can exploit this path. The nonce required is the subaccount's public nonce stored in `nonces`, which is observable on-chain. No privileged access beyond holding the current linked signer key is required. [6](#0-5) 

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in the fast-mode path, so only the subaccount owner's key can authorize a linked signer change:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only owner may change linked signer
);
```

Optionally, implement a two-step acceptance: the nominated new signer must submit a confirmation transaction before `linkedSigners` is updated.

---

### Proof of Concept

1. Alice (subaccount owner) submits a `LinkSigner` transaction setting Bob's address as her linked signer. `linkedSigners[Alice_subaccount] = Bob`.
2. Bob (malicious linked signer) constructs a `SignedLinkSigner` payload: `sender = Alice_subaccount`, `signer = Bob2` (a second address Bob controls), `nonce = nonces[Alice_address]` (read from chain).
3. Bob signs this payload with his current linked signer key and submits it to the sequencer.
4. `validateSignedTx(..., true)` accepts Bob's signature because `getLinkedSigner(Alice_subaccount)` returns Bob.
5. `linkedSigners[Alice_subaccount]` is now set to `Bob2`.
6. Alice, unaware of the reassignment, believes Bob's original key is the active signer. Bob2 now has full signing authority and can submit withdrawals, liquidations, and transfers on Alice's behalf. [1](#0-0)

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

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
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

**File:** core/contracts/EndpointStorage.sol (L50-51)
```text
    mapping(bytes32 => address) internal linkedSigners;

```
