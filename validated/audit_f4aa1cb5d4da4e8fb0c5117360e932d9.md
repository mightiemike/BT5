### Title
Linked Signer Can Overwrite Its Own Authorization Entry, Enabling Account Takeover - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` fast-path transaction is validated with `allowLinkedSigner = true`. This means the current linked signer — a delegated key with no ownership rights — can sign a new `LinkSigner` transaction to replace itself with any attacker-controlled address. A compromised linked signer (e.g., a leaked API key) can permanently escalate to full signing authority over the subaccount, enabling collateral withdrawal and arbitrary order placement.

---

### Finding Description

The `linkedSigners` mapping in `EndpointStorage` stores a delegated address that is permitted to sign transactions on behalf of a subaccount. It is the protocol's equivalent of a private key file: whoever controls it controls the subaccount.

In `processTransactionImpl` (the sequencer fast path), the `LinkSigner` case is:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true          // ← allowLinkedSigner
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateCompactSignature`, which passes the current linked signer to `verifier.validateCompactSignature`:

```solidity
verifier.validateCompactSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

The verifier accepts a signature from **either** the subaccount owner address **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the current linked signer can sign a `LinkSigner` transaction that writes a new address into `linkedSigners[subaccount]` — including an attacker-controlled address.

**Contrast with the slow-mode path**, which correctly restricts `LinkSigner` to the subaccount owner only via `validateSender`:

```solidity
validateSender(txn.sender, sender);   // msg.sender must == address(bytes20(subaccount))
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

`validateSender` only accepts the owner address embedded in the subaccount, never the linked signer:

```solidity
require(
    address(uint160(bytes20(txSender))) == sender ||
        sender == address(this),
    ERR_SLOW_MODE_WRONG_SENDER
);
``` [5](#0-4) 

The fast path and slow path are inconsistent: the slow path correctly treats `LinkSigner` as an owner-only operation; the fast path grants the same power to the delegated key.

---

### Impact Explanation

`linkedSigners` is the sole on-chain record of who may sign on behalf of a subaccount. Once an attacker overwrites it with their own address, they can sign any sequencer-submitted transaction for that subaccount — including `WithdrawCollateral`, `TransferQuote`, and order placement — with no further interaction from the legitimate owner. The owner cannot recover without submitting a slow-mode `LinkSigner` (3-day delay), during which the attacker can drain all collateral. This is a direct, complete account takeover with full asset loss. [6](#0-5) 

---

### Likelihood Explanation

Linked signers are the standard mechanism for API/bot trading in this protocol. Any user who has set a linked signer and whose linked signer key is compromised (leaked `.env`, stolen from a trading server, phished via a malicious dApp) is immediately vulnerable. The attacker only needs to submit one crafted `LinkSigner` transaction to the sequencer's public API endpoint — no privileged access, no admin keys, no governance. The sequencer will include it because it carries a valid signature from the current linked signer. [7](#0-6) 

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` case in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This aligns the fast path with the slow path's existing correct behavior. A linked signer is a delegated operational key; it must not be able to modify its own delegation entry. [1](#0-0) 

---

### Proof of Concept

1. Alice owns subaccount `alice_addr ++ "default\x00\x00\x00\x00"` and has set `linkedSigners[alice_subaccount] = bot_key`.
2. Attacker obtains `bot_key` (leaked from Alice's trading server).
3. Attacker constructs:
   ```
   SignedLinkSigner {
     tx: LinkSigner {
       sender: alice_subaccount,
       signer: bytes32(attacker_address),   // pad to 32 bytes
       nonce:  current_nonce_for_alice
     },
     signature: sign(digest, bot_key)
   }
   ```
4. Attacker submits this transaction to the sequencer's off-chain API. The sequencer includes it in the next `submitTransactionsChecked` batch.
5. `processTransactionImpl` → `validateSignedTx(..., true)` → `verifier.validateCompactSignature` recovers `bot_key`, which equals `getLinkedSigner(alice_subaccount)` → passes.
6. `linkedSigners[alice_subaccount]` is now `attacker_address`.
7. Attacker signs a `WithdrawCollateral` transaction for Alice's subaccount and submits it. All collateral is transferred to the attacker. [1](#0-0) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L237-239)
```text
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

**File:** core/contracts/Verifier.sol (L312-318)
```text
        address recovered = ECDSA.recover(digest, signature.r, signature.vs);
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
