### Title
Linked Signer Can Permanently Hijack Subaccount by Re-Linking to Attacker Address — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction handler in `EndpointTx.processTransactionImpl` passes `allowLinkedSigner = true` to `validateSignedTx`. This means the **current linked signer** (a session key) is accepted as a valid authorizer for a `LinkSigner` transaction that replaces itself. A compromised session key can therefore permanently redirect signing authority to an attacker-controlled address, after which the attacker can drain all collateral from the subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch is:

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

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes the current linked signer to `verifier.validateSignature`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`verifier.validateSignature` accepts the signature if it comes from **either** the subaccount owner **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The result: the linked signer can sign a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address, including an attacker-controlled one. Once overwritten, the attacker holds full signing authority and can sign `WithdrawCollateral`, `LiquidateSubaccount`, `TransferQuote`, `BurnNlp`, etc.

The slow-mode path for `LinkSigner` does **not** share this flaw — it uses `validateSender`, which enforces that only the subaccount owner's address can submit the transaction:

```solidity
validateSender(txn.sender, sender);
...
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

This asymmetry confirms the fast-path `LinkSigner` is the defective branch.

---

### Impact Explanation

Once the attacker's address is installed as the linked signer, it is accepted by every signed transaction type that passes `allowLinkedSigner = true`: `WithdrawCollateral`, `WithdrawCollateralV2`, `LiquidateSubaccount`, `MintNlp`, `BurnNlp`, `TransferQuote`, and `LinkSigner` itself. The attacker can:

1. Sign a `WithdrawCollateral` or `WithdrawCollateralV2` transaction to drain all spot collateral to an arbitrary `sendTo` address.
2. Continuously re-sign `LinkSigner` to prevent the victim from recovering via slow mode (the attacker can submit a new fast-path `LinkSigner` before the victim's slow-mode transaction executes, since slow-mode has a 3-day delay).

The corrupted state is `linkedSigners[victim_subaccount]`. The asset delta is the full collateral balance of the subaccount. [5](#0-4) 

---

### Likelihood Explanation

Linked signers are the standard mechanism for automated trading bots and session keys in the Nado protocol. Session keys are routinely stored in server-side environments with a larger attack surface than a hardware wallet. Any attacker who obtains a session key's private key — through server compromise, key leakage, or phishing — can immediately and permanently escalate to full subaccount control. No admin access, sequencer compromise, or governance action is required. The sequencer will process the malicious `LinkSigner` transaction as a normal batch operation.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address encoded in the first 20 bytes of the `sender` field) should be permitted to authorize a change to the linked signer. This matches the restriction already enforced on the slow-mode path via `validateSender`.

```solidity
// EndpointTx.sol — processTransactionImpl, LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← only owner may re-link
);
``` [1](#0-0) 

---

### Proof of Concept

1. Alice calls `depositCollateral` and links a session key `SK` via `LinkSigner` (signed by her wallet).
2. Attacker obtains `SK`'s private key (e.g., server breach).
3. Attacker constructs a `LinkSigner` transaction: `sender = alice_subaccount`, `signer = bytes32(attacker_address)`, `nonce = current_nonce`.
4. Attacker signs the EIP-712 digest with `SK`.
5. Sequencer includes the transaction in a batch; `processTransactionImpl` validates the signature against the current linked signer (`SK`) — passes — and writes `linkedSigners[alice_subaccount] = attacker_address`.
6. Attacker signs a `WithdrawCollateralV2` transaction with `sendTo = attacker_wallet`, draining Alice's collateral.
7. Alice attempts a slow-mode `LinkSigner` to recover; attacker front-runs by submitting another fast-path `LinkSigner` before the 3-day delay expires, maintaining control. [6](#0-5) [7](#0-6)

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

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
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
