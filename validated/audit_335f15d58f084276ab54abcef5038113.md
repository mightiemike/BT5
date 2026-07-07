### Title
Linked Signer Can Overwrite Its Own Authorization State via `LinkSigner` Transaction, Enabling Subaccount Takeover — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, `LinkSigner` transactions are validated with `allowLinkedSigner = true`, meaning the current linked signer can sign a `LinkSigner` transaction to replace itself with any address. The `linkedSigners` mapping — which controls who can authorize withdrawals, transfers, and liquidations on behalf of a subaccount — is stored in contract storage without restricting write access to the subaccount owner. This is a direct analog to the external report's plaintext key storage issue: sensitive authorization state is held in accessible, unprotected storage that an unauthorized party (the linked signer) can overwrite.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch (lines 576–590) validates the transaction with `allowLinkedSigner = true`:

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

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature(sender, getLinkedSigner(sender), digest, signature)`, which accepts a signature from either the subaccount owner **or** the current linked signer:

```solidity
function validateSignature(...) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`verifier.validateSignature` accepts the signature if it recovers to either the owner address or the linked signer address: [3](#0-2) 

This means the current linked signer can sign a `LinkSigner` transaction to change `linkedSigners[subaccount]` to any address it chooses.

**This is directly inconsistent with the slow-mode `LinkSigner` path** (lines 232–239), which uses `validateSender(txn.sender, sender)` — a check that only the address embedded in the subaccount (the owner) can submit:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

`validateSender` enforces owner-only access: [5](#0-4) 

The slow-mode path correctly restricts `LinkSigner` to the subaccount owner. The normal sequencer path does not. The `linkedSigners` mapping is the authorization-critical state that determines who can sign `WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, and other sensitive transactions on behalf of a subaccount: [6](#0-5) 

---

### Impact Explanation

The corrupted state is `linkedSigners[subaccount]` — the mapping that controls signing authority for the subaccount. A malicious or compromised linked signer can:

1. Sign a `LinkSigner` transaction pointing to an attacker-controlled address.
2. The sequencer includes this transaction in a batch (it is a structurally valid signed transaction).
3. `linkedSigners[victim_subaccount]` is overwritten with the attacker's address.
4. The attacker signs `WithdrawCollateral` or `WithdrawCollateralV2` transactions (both validated with `allowLinkedSigner = true`) to drain all collateral from the subaccount.

The asset delta is the full collateral balance of the victim subaccount. The owner's only recourse is to submit a competing `LinkSigner` transaction via their own key before the attacker drains the account — a race condition the attacker can win by acting immediately after the hijack is confirmed on-chain.

---

### Likelihood Explanation

Linked signers are the standard mechanism for automated trading bots and third-party trading services in this protocol. A user who links their subaccount to any third-party service is exposed to this risk if that service is malicious or is itself compromised. The attack requires no special privileges beyond controlling the linked signer's key — a key the user has already explicitly registered on-chain. The entry path is the normal sequencer batch submission (`submitTransactionsChecked`), which the sequencer will process for any structurally valid signed transaction.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`. Only the subaccount owner (the address embedded in the subaccount `bytes32`) should be able to change the linked signer. This aligns the normal sequencer path with the slow-mode path, which already correctly enforces owner-only access for this operation.

---

### Proof of Concept

1. Alice has `alice_subaccount` (bytes32 with Alice's address in the first 20 bytes) with `bot_address` as the linked signer.
2. `bot_address` (malicious) constructs and signs a `LinkSigner` transaction: `{sender: alice_subaccount, signer: attacker_address, nonce: current_nonce}`.
3. The sequencer includes this transaction in a batch via `submitTransactionsChecked`.
4. `processTransactionImpl` reaches the `LinkSigner` branch, calls `validateSignedTx(..., true)`, and `bot_address`'s signature is accepted because `getLinkedSigner(alice_subaccount) == bot_address`.
5. `linkedSigners[alice_subaccount]` is set to `attacker_address`.
6. The attacker immediately signs a `WithdrawCollateral` transaction for the full balance of `alice_subaccount`; the sequencer includes it; the collateral is drained.
7. Alice's only recourse — submitting a corrective `LinkSigner` via her own key — arrives too late.

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
