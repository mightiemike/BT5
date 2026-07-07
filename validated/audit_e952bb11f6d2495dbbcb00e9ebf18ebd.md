### Title
Linked Signer Can Arbitrarily Reassign Itself to Attacker-Controlled Address - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction type in `EndpointTx.processTransactionImpl` accepts a signature from the **current linked signer** as valid authorization to change the linked signer for a subaccount to any arbitrary address. This is because `validateSignedTx` is called with `allowLinkedSigner = true`, meaning the linked signer — a semi-privileged entity — can silently replace itself with an attacker-controlled address without the subaccount owner's consent.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch decodes a `SignedLinkSigner` transaction and calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` routes through `validateSignature`, which passes the current linked signer address to the verifier as an accepted signer: [2](#0-1) 

This means the current linked signer can craft and sign a `SignedLinkSigner` transaction targeting `signedTx.tx.sender` (the victim subaccount) and set `signedTx.tx.signer` to any arbitrary address. The nonce used is the subaccount owner's nonce (public state), which the linked signer can trivially read.

By contrast, the slow-mode path for `LinkSigner` correctly uses `validateSender`, which enforces that `msg.sender` must be the address embedded in the subaccount bytes — i.e., the actual owner: [3](#0-2) 

The sequencer path has no equivalent owner-only guard for this transaction type.

The `linkedSigners` mapping is the sole authorization source for all sequencer-path transactions that pass `allowLinkedSigner = true`, including `WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, and others: [4](#0-3) 

---

### Impact Explanation

A linked signer that is compromised or acts maliciously can:

1. Sign a `LinkSigner` transaction pointing `signedTx.tx.signer` to an attacker-controlled address.
2. Once the sequencer includes this transaction, `linkedSigners[victim_subaccount]` is overwritten with the attacker's address.
3. The attacker's address is now the accepted signer for all `allowLinkedSigner = true` transaction types, including `WithdrawCollateral` and `TransferQuote`.
4. The attacker drains the victim subaccount's collateral.
5. The original owner has no on-chain mechanism to detect or prevent this before the drain, since the sequencer processes the batch atomically.

The corrupted state is `linkedSigners[subaccount]` — a direct authorization anchor for asset withdrawal.

---

### Likelihood Explanation

- Linked signers are a standard feature used by traders running automated bots or API keys; many production subaccounts will have one set.
- The attack requires only that the linked signer's private key be compromised (e.g., leaked API key, compromised hot wallet, or a malicious third-party signer service).
- The sequencer has no reason to reject the transaction — it is structurally valid and passes all on-chain signature checks.
- The attacker needs only the victim's current nonce (public state via `nonces` mapping) and the linked signer's private key.
- No owner interaction or governance capture is required.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`:

```solidity
// Before (vulnerable):
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← allows linked signer to sign
);

// After (fixed):
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // ← only subaccount owner may change linked signer
);
```

This mirrors the slow-mode path's intent, where `validateSender` enforces owner-only authorization for `LinkSigner`.

---

### Proof of Concept

1. Alice owns subaccount `0xAlice000...` and sets Bob (`0xBob`) as her linked signer via a slow-mode `LinkSigner` transaction.
2. Bob's private key is compromised by Mallory.
3. Mallory reads `nonces[address(0xAlice000...)]` from chain — say it is `N`.
4. Mallory crafts `SignedLinkSigner { tx: { sender: 0xAlice000..., nonce: N, signer: 0xMallory }, signature: <signed by 0xBob> }`.
5. Mallory submits this to the sequencer's off-chain intake.
6. The sequencer includes it in the next batch via `submitTransactionsChecked`.
7. `validateSignedTx(..., allowLinkedSigner=true)` passes: Bob is the current linked signer and signed the digest.
8. `linkedSigners[0xAlice000...] = 0xMallory` is written.
9. Mallory immediately signs a `WithdrawCollateral` transaction (also `allowLinkedSigner = true`) and submits it.
10. Alice's collateral is transferred to Mallory's address. [5](#0-4)

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

**File:** core/contracts/EndpointStorage.sol (L50-51)
```text
    mapping(bytes32 => address) internal linkedSigners;

```
