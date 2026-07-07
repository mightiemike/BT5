### Title
Linked Signer Can Reassign Its Own Authority via `LinkSigner`, Hijacking Parent and All Isolated Subaccounts — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **current linked signer** — a delegated, mid-level signing authority — can sign a `LinkSigner` transaction to replace itself with any arbitrary address, without the original subaccount owner's consent. Because isolated subaccounts inherit their signing authority from the parent's `linkedSigners` mapping, the new address immediately gains signing authority over the parent subaccount and every isolated subaccount under it. The new linked signer can then sign `TransferQuote` (also `allowLinkedSigner = true`) to move funds to an attacker-controlled subaccount.

---

### Finding Description

The `LinkSigner` transaction is intended to let a subaccount **owner** delegate signing authority to a trusted address. The owner signs the `LinkSigner` payload and the mapping is updated:

```
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
```

In the fast path (`processTransactionImpl`), the handler calls:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true          // ← allowLinkedSigner
);
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted signer:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` accepts either the address embedded in the `sender` bytes32 **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
    ((recovered == address(uint160(bytes20(sender)))) || (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

So the current linked signer can produce a valid `LinkSigner` signature and overwrite `linkedSigners[parentSubaccount]` to any address it chooses.

For isolated subaccounts, `getLinkedSigner` always reads from the **parent's** `linkedSigners` entry:

```solidity
return RiskHelper.isIsolatedSubaccount(subaccount)
    ? linkedSigners[IOffchainExchange(offchainExchange).getParentSubaccount(subaccount)]
    : linkedSigners[subaccount];
``` [4](#0-3) 

There is no separate `linkedSigners` slot for isolated subaccounts. Changing the parent's linked signer therefore immediately changes the effective signer for every isolated subaccount under it. [5](#0-4) 

The `TransferQuote` transaction type is also validated with `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true
);
``` [6](#0-5) 

This means the newly installed linked signer can immediately sign `TransferQuote` to move funds from the parent (or any isolated subaccount) to an attacker-controlled subaccount.

---

### Impact Explanation

**Concrete state delta:** `linkedSigners[victimParentSubaccount]` is overwritten to an attacker-controlled address. All isolated subaccounts under that parent immediately become signable by the attacker. The attacker can then drain the parent and all isolated subaccounts via `TransferQuote` to their own subaccount, followed by `WithdrawCollateral`.

The original owner retains the ability to sign directly (the address embedded in the bytes32 is always accepted), but the attacker can front-run or race the owner before the owner notices the linked signer was changed.

---

### Likelihood Explanation

Any user who has ever set a linked signer is exposed. The linked signer is commonly set to an API key or hot wallet for automated trading — a realistic threat model where the hot wallet is compromised or acts maliciously. The attack requires only two sequencer-submitted transactions (`LinkSigner` then `TransferQuote`), both of which are standard fast-path operations. No special privileges beyond holding the current linked signer key are required.

---

### Recommendation

`LinkSigner` should **not** allow the current linked signer to sign it. Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [7](#0-6) 

This matches the slow-path `LinkSigner` handler, which already enforces owner-only authorization via `validateSender`:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [8](#0-7) 

---

### Proof of Concept

1. Alice owns `subaccountA` (bytes32 with Alice's address in top 20 bytes) and sets Bob's address as linked signer: `linkedSigners[subaccountA] = Bob`.
2. Alice creates isolated subaccounts `iso1`, `iso2` under `subaccountA`. Their effective signer is `linkedSigners[subaccountA]` = Bob.
3. Bob (the linked signer) constructs a `SignedLinkSigner` payload: `{ sender: subaccountA, signer: Attacker, nonce: N }` and signs it with Bob's key.
4. Bob submits this to the sequencer as a fast-path `LinkSigner` transaction. `validateSignedTx` accepts Bob's signature because `allowLinkedSigner = true` and `getLinkedSigner(subaccountA) == Bob`.
5. `linkedSigners[subaccountA]` is now set to `Attacker`.
6. `getLinkedSigner(iso1)` and `getLinkedSigner(iso2)` now return `Attacker` (they read from the parent's slot).
7. Attacker signs `TransferQuote` transactions from `subaccountA`, `iso1`, and `iso2` to `Attacker`'s own subaccount. All pass `validateSignedTx` with `allowLinkedSigner = true`.
8. Attacker withdraws the transferred funds via `WithdrawCollateral`.

Alice's funds across the parent and all isolated subaccounts are drained before she can react.

### Citations

**File:** core/contracts/EndpointTx.sol (L149-157)
```text
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

**File:** core/contracts/EndpointTx.sol (L599-614)
```text
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

**File:** core/contracts/OffchainExchange.sol (L1055-1065)
```text
            // |  address | reserved | productId |   id   |  'iso'  |
            // | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
            newIsolatedSubaccount = bytes32(
                (uint256(uint160(senderAddress)) << 96) |
                    (uint256(txn.productId) << 32) |
                    (uint256(id) << 24) |
                    6910831
            );
            isolatedSubaccountsMask[senderAddress] |= 1 << id;
            parentSubaccounts[newIsolatedSubaccount] = txn.order.sender;
            isolatedSubaccounts[txn.order.sender][id] = newIsolatedSubaccount;
```
