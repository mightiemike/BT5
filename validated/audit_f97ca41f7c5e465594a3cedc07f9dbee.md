### Title
Linked Signer Can Unilaterally Replace Itself via `LinkSigner`, Enabling Full Subaccount Collateral Theft - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.sol`, the fast-path handler for `TransactionType.LinkSigner` calls `validateSignedTx` with `allowLinkedSigner = true`. This permits the currently linked signer to sign a `LinkSigner` transaction that replaces itself with any arbitrary address. Once replaced, the attacker-controlled address inherits full linked-signer authority and can sign `WithdrawCollateral` transactions to drain all collateral from the victim subaccount — without the subaccount owner's knowledge or consent.

---

### Finding Description

The `processTransactionImpl` function in `EndpointTx.sol` handles the sequencer-submitted fast-path `LinkSigner` transaction as follows:

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

`validateSignedTx` with `allowLinkedSigner = true` routes into `validateSignature`, which accepts a valid signature from **either** the subaccount owner address **or** the currently registered linked signer:

```solidity
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

This means the linked signer can craft and sign a `LinkSigner` transaction that sets `signedTx.tx.signer` to any address — including one the original linked signer (or a colluding attacker) controls — and the sequencer will accept it, overwriting `linkedSigners[subaccount]`. [3](#0-2) 

**Contrast with the slow-mode path**, which correctly restricts `LinkSigner` to the subaccount owner only:

```solidity
validateSender(txn.sender, sender);   // msg.sender must equal address(uint160(bytes20(txSender)))
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The slow-mode path enforces that only the EOA derived from the subaccount bytes32 can change the linked signer. The fast-path has no such restriction, creating an asymmetric and exploitable inconsistency.

---

### Impact Explanation

Once the linked signer replaces itself with an attacker-controlled address, the attacker inherits all linked-signer privileges. The fast-path `WithdrawCollateral` and `WithdrawCollateralV2` handlers both call `validateSignedTx` with `allowLinkedSigner = true`: [5](#0-4) [6](#0-5) 

The attacker can therefore sign `WithdrawCollateral` transactions to drain the full collateral balance of the victim subaccount to an address they control. Additional linked-signer-permitted operations include `LiquidateSubaccount`, `TransferQuote`, `MintNlp`, and `BurnNlp` — all called with `allowLinkedSigner = true`.

**Corrupted state**: `linkedSigners[subaccount]` is mutated from a trusted address to an attacker address, followed by complete collateral theft via `clearinghouse.withdrawCollateral`.

---

### Likelihood Explanation

Any user who has ever called `LinkSigner` to delegate signing authority (e.g., to a trading bot, API key, or third-party service) is exposed. This is a standard and documented use case of the protocol. The attack requires only that the linked signer (or a party who compromises it) submit a single signed `LinkSigner` transaction through the sequencer. No admin access, governance capture, or special privilege is needed beyond possession of the linked signer's private key — which is exactly the threat model for a compromised or malicious delegated signer.

---

### Recommendation

The `LinkSigner` fast-path handler should be called with `allowLinkedSigner = false`, restricting signing authority for this operation exclusively to the subaccount owner:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [7](#0-6) 

This mirrors the invariant already enforced in the slow-mode path and ensures that delegation of signing authority can only be granted or revoked by the subaccount owner, not by the delegate itself.

---

### Proof of Concept

1. Alice (subaccount owner, address `0xAlice`) deposits collateral and links a trading bot (`0xBot`) via `LinkSigner`. State: `linkedSigners[aliceSubaccount] = 0xBot`.
2. `0xBot` (or an attacker who compromises `0xBot`'s key) constructs a `SignedLinkSigner` transaction with `tx.sender = aliceSubaccount`, `tx.signer = bytes32(uint256(uint160(0xAttacker)))`, and signs it with `0xBot`'s key.
3. The sequencer submits this transaction to `processTransactionImpl`. `validateSignedTx` with `allowLinkedSigner = true` recovers `0xBot` from the signature, which matches `linkedSigners[aliceSubaccount]` — validation passes.
4. State is updated: `linkedSigners[aliceSubaccount] = 0xAttacker`.
5. `0xAttacker` constructs a `SignedWithdrawCollateral` transaction for `aliceSubaccount` and signs it with their own key.
6. The sequencer submits it; `validateSignedTx` with `allowLinkedSigner = true` recovers `0xAttacker`, which now matches `linkedSigners[aliceSubaccount]` — validation passes.
7. `clearinghouse.withdrawCollateral` transfers Alice's entire collateral balance to `0xAttacker`. [1](#0-0) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L418-436)
```text
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
