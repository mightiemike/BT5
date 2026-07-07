### Title
Linked Signer Can Overwrite `linkedSigners` to Attacker-Controlled Address — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` branch in `processTransactionImpl` calls `validateSignedTx` with `allowLinkedSigner = true`. This means the currently linked signer — not just the subaccount owner — can sign and submit a new `LinkSigner` transaction that overwrites `linkedSigners[victim_subaccount]` with any attacker-controlled address. Once the overwrite succeeds, the attacker gains permanent signing authority and can submit `WithdrawCollateral`, `MintNlp`, `BurnNlp`, and `TransferQuote` on behalf of the victim.

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
        true          // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

And `Verifier.validateSignature` accepts the signature if the recovered address equals **either** the subaccount owner **or** the current linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

There is no additional guard requiring that `LinkSigner` mutations must be authorized by the subaccount owner (`address(bytes20(sender))`). Any currently linked signer can therefore sign a new `LinkSigner` payload and overwrite `linkedSigners[victim_subaccount]` with an arbitrary address.

**Contrast with the slow-mode path**, which correctly enforces owner-only authorization for `LinkSigner`:

```solidity
validateSender(txn.sender, sender);   // msg.sender must be the subaccount owner
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The sequencer path lacks this owner-only constraint.

---

### Impact Explanation

After the overwrite, `linkedSigners[victim_subaccount] == attacker2`. Every subsequent `validateSignedTx(..., true)` call for the victim's subaccount will accept signatures from `attacker2`. This covers:

- `WithdrawCollateral` / `WithdrawCollateralV2` — direct asset theft
- `MintNlp` / `BurnNlp` — manipulation of NLP pool positions
- `TransferQuote` — quote balance exfiltration
- `LiquidateSubaccount` — attacker can liquidate the victim's positions

The attacker also retains the ability to re-link again at will, making the takeover persistent and irrevocable without victim action.

---

### Likelihood Explanation

The precondition — that the victim has previously linked the attacker as a signer — is realistic. Users routinely link signers to enable trading bots, third-party integrations, or session keys. Once linked for any legitimate purpose, the linked signer can immediately escalate to full permanent control. No additional privileges, admin access, or sequencer compromise are required; the attacker only needs to submit a sequencer-batched `LinkSigner` transaction signed by their already-linked EOA.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (`address(bytes20(sender))`) should be permitted to alter the signer relationship:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only subaccount owner may change the linked signer
);
``` [5](#0-4) 

This aligns the sequencer path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner`.

---

### Proof of Concept

1. Deploy `Endpoint` + `EndpointTx` + `Verifier` on a local Hardhat fork.
2. Victim (`victim_owner`) creates subaccount `victim_sub = bytes32(bytes20(victim_owner)) | subaccountId`.
3. Victim signs and submits a `LinkSigner` tx linking `attacker_EOA`: `linkedSigners[victim_sub] = attacker_EOA`.
4. Attacker constructs a new `SignedLinkSigner` payload:
   - `tx.sender = victim_sub`
   - `tx.signer = bytes32(bytes20(attacker2))`
   - `tx.nonce = nonces[victim_owner]` (current nonce)
   - `signature` = EIP-712 signature over `LinkSigner(victim_sub, attacker2, nonce)` signed by `attacker_EOA`
5. Sequencer batches and calls `processTransactionImpl` with this payload.
6. `validateSignedTx` passes because `ECDSA.recover(digest, sig) == attacker_EOA == getLinkedSigner(victim_sub)`.
7. `linkedSigners[victim_sub]` is overwritten with `attacker2`.
8. Assert `linkedSigners[victim_sub] == attacker2`.
9. Attacker constructs a `SignedWithdrawCollateral` signed by `attacker2`; `validateSignedTx(..., true)` passes, and `clearinghouse.withdrawCollateral` drains the victim's balance.

### Citations

**File:** core/contracts/EndpointTx.sol (L178-183)
```text
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
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
