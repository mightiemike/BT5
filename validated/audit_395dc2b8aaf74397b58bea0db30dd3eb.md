### Title
Linked Signer Can Unilaterally Reassign Subaccount Signing Authority to Arbitrary Address — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to replace the linked signer with any arbitrary address. The new linked signer immediately inherits full signing authority over the subaccount, including the ability to sign `WithdrawCollateral`, `LiquidateSubaccount`, `TransferQuote`, and `BurnNlp` transactions.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch is:

```solidity
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

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted signer:

```solidity
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` accepts a signature from either the subaccount owner address or the `linkedSigner`:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the **current linked signer** can produce a valid `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address of their choosing.

By contrast, the slow-mode path for `LinkSigner` uses `validateSender`, which enforces that only the subaccount owner (`msg.sender`) can change the linked signer:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The asymmetry between the two paths reveals that the sequencer path's `allowLinkedSigner = true` for `LinkSigner` is inconsistent with the intended ownership model.

---

### Impact Explanation

A malicious or compromised linked signer (e.g., a trading bot key) can:

1. Sign a `LinkSigner` transaction pointing `signedTx.tx.signer` to an attacker-controlled address.
2. The sequencer processes this, writing `linkedSigners[victim_subaccount] = attacker_address`.
3. The attacker address immediately signs a `WithdrawCollateral` or `WithdrawCollateralV2` transaction (also validated with `allowLinkedSigner = true`) to drain the subaccount.

All subsequent signed transaction types that use `allowLinkedSigner = true` — including `LiquidateSubaccount`, `MintNlp`, `BurnNlp`, `TransferQuote`, and `LinkSigner` itself — are now under attacker control.

The corrupted state is: `linkedSigners[subaccount]` is overwritten to an attacker address without the subaccount owner's knowledge or consent.

---

### Likelihood Explanation

Linked signers are commonly automated keys (trading bots, API keys, hot wallets). A compromised or malicious linked signer key is a realistic threat. The attack requires no owner/admin access, no sequencer compromise, and no governance action — only a valid signature from the current linked signer, which is the normal operational credential for that key. The sequencer processes the transaction in its normal flow.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`, consistent with the slow-mode path. Only the subaccount owner (the address embedded in the `sender` bytes32) should be permitted to change the linked signer:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only owner can change linked signer
);
``` [5](#0-4) 

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and links Bob's key as her linked signer via a valid `LinkSigner` transaction.
2. Bob (malicious) constructs `LinkSigner{sender: alice_subaccount, signer: charlie_address, nonce: N}` and signs it with Bob's key.
3. Bob submits this to the sequencer off-chain. The sequencer includes it in a batch and calls `submitTransactionsChecked`.
4. `processTransactionImpl` validates the signature — Bob's key matches `getLinkedSigner(alice_subaccount)` — and writes `linkedSigners[alice_subaccount] = charlie_address`.
5. Charlie constructs `WithdrawCollateral{sender: alice_subaccount, productId: USDC, amount: full_balance, nonce: N+1}` and signs it with Charlie's key.
6. The sequencer processes this; Charlie's key matches the new `linkedSigners[alice_subaccount]`; Alice's collateral is withdrawn to Charlie's address.

Alice's only recourse is to submit a slow-mode `LinkSigner` transaction (3-day delay) or a sequencer-path `LinkSigner` signed by her own key — but by the time she detects the change, her funds may already be drained.

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
