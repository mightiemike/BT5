### Title
Linked Signer Can Self-Escalate via `LinkSigner` Transaction, Bypassing Owner Authorization — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction in `EndpointTx.processTransactionImpl` is processed with `allowLinkedSigner=true`, meaning the currently-linked signer's signature is accepted to authorize a change to the linked signer itself. A compromised session key (linked signer) can therefore replace itself with an attacker-controlled address, permanently escalating control over the subaccount without the owner's involvement. The attacker's new linked signer can then sign `WithdrawCollateral` or `TransferQuote` to drain the subaccount's collateral.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with the fifth argument hardcoded to `true`:

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

`validateSignedTx` calls `validateSignature`, which in turn calls `verifier.validateSignature` passing the linked signer when `allowLinkedSigner=true`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` accepts the recovered address if it matches **either** the subaccount owner **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Because `LinkSigner` is the transaction that **sets** the linked signer, accepting the linked signer's own signature for this operation creates a circular authorization: the session key can authorize changes to itself. There is no restriction on what address `signedTx.tx.signer` can be set to.

**Analog mapping**: The external report describes trusting `onAuthenticationSucceeded` without the `CryptoObject` — the cryptographic object that binds authentication to the actual biometric key. In Nado, the `CryptoObject` analog is the **owner's private key**: the cryptographic object that should be required to authorize changes to signing authority. Just as the mobile app accepts the callback result without the binding object, Nado accepts the linked signer's signature for `LinkSigner` without requiring the owner's key.

By contrast, `WithdrawCollateralV2` correctly restricts linked signers when a non-zero `sendTo` is specified, demonstrating the protocol is aware of the security boundary:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    signedTx.tx.sendTo == address(0)  // linked signer blocked when sendTo != 0
);
``` [4](#0-3) 

No equivalent restriction exists for `LinkSigner`.

---

### Impact Explanation

A compromised linked signer can:

1. Sign a `LinkSigner` transaction replacing itself with an attacker-controlled address B.
2. Use B to sign `WithdrawCollateral` (V1, `allowLinkedSigner=true`) to drain the subaccount's collateral to the default recipient.
3. Alternatively use B to sign `TransferQuote` to move quote tokens to an attacker-controlled subaccount.

All of `WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, `MintNlp`, and `BurnNlp` accept linked signer signatures, so the attacker gains full operational control over the subaccount after step 1. [5](#0-4) 

The corrupted state is: `linkedSigners[victim_subaccount]` is permanently set to an attacker address, enabling unauthorized collateral withdrawal and quote transfer from the victim's subaccount.

---

### Likelihood Explanation

**Medium.** Linked signers are session keys used by trading bots, frontend applications, and automated strategies. They are stored in environments (browsers, servers, CI pipelines) that are materially more exposed than hardware wallets or cold-storage owner keys. A leaked or compromised session key is a realistic threat model for any active trader. The attacker only needs to submit one sequencer-processed `LinkSigner` transaction before the owner can react.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `EndpointTx.processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // owner signature required to change linked signer
);
``` [6](#0-5) 

This ensures only the subaccount owner (the address encoded in the first 20 bytes of `sender`) can authorize changes to signing authority, matching the security model already applied to `WithdrawCollateralV2` with a non-zero `sendTo`.

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and sets linked signer to address `A` (session key) by signing a `LinkSigner` transaction with her owner key. `linkedSigners[alice_subaccount] = A`.
2. Attacker obtains `A`'s private key (e.g., leaked from a trading bot server).
3. Attacker uses `A` to sign a new `LinkSigner` transaction: `{ sender: alice_subaccount, signer: attacker_address_B, nonce: current_nonce }`.
4. Sequencer processes the transaction. `validateSignedTx` recovers `A`, which matches `linkedSigners[alice_subaccount]`, so validation passes. `linkedSigners[alice_subaccount] = B`.
5. Attacker uses `B` to sign `WithdrawCollateral`: `{ sender: alice_subaccount, productId: QUOTE_PRODUCT_ID, amount: full_balance, nonce: next_nonce }`.
6. `validateSignedTx` recovers `B`, which now matches `linkedSigners[alice_subaccount]`. Validation passes. `clearinghouse.withdrawCollateral` drains Alice's collateral. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
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

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
