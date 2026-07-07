### Title
Linked Signer Can Replace Itself via `LinkSigner`, Enabling Full Subaccount Takeover — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The fast-path `LinkSigner` handler in `EndpointTx.processTransactionImpl` passes `allowLinkedSigner = true` to `validateSignedTx`. This means the currently-registered linked signer for a subaccount can sign a new `LinkSigner` transaction to replace itself with any arbitrary address — including an attacker-controlled one — without any action from the subaccount owner. Once replaced, the new linked signer can sign `WithdrawCollateral`, `TransferQuote`, and other fund-moving transactions to drain the subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated as follows:

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
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which in turn calls `verifier.validateSignature` with the current linked signer as an accepted signer:

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

`Verifier.validateSignature` accepts the signature if it recovers to either the subaccount owner address **or** the linked signer:

```solidity
function validateSignature(...) public pure {
    address recovered = ECDSA.recover(digest, signature);
    require(
        (recovered != address(0)) &&
            ((recovered == address(uint160(bytes20(sender)))) ||
                (recovered == linkedSigner)),
        ERR_INVALID_SIGNATURE
    );
}
``` [3](#0-2) 

This means the linked signer — a delegated key intended for trading operations — can sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address it chooses.

By contrast, the slow-mode path for `LinkSigner` correctly restricts this operation to the subaccount owner only, using `validateSender` which checks `msg.sender == address(uint160(bytes20(txSender)))`: [4](#0-3) 

The asymmetry between the two paths is the root cause.

---

### Impact Explanation

Once the linked signer replaces itself with an attacker address, the attacker's address becomes the accepted signer for the subaccount. The attacker can then sign:

- `WithdrawCollateral` (fast-path, `allowLinkedSigner = true`) — drains collateral to any address
- `WithdrawCollateralV2` (fast-path, `allowLinkedSigner = true` when `sendTo == address(0)`) — same
- `TransferQuote` (fast-path, `allowLinkedSigner = true`) — transfers quote balance to another subaccount [5](#0-4) [6](#0-5) 

The subaccount owner loses all collateral with no on-chain action required from them. The corrupted state is `linkedSigners[subaccount]` and the resulting `spotEngine` balance delta from the unauthorized withdrawal.

---

### Likelihood Explanation

Any party that holds a linked signer key — a trading bot, a third-party API service, or any key that was ever linked — can execute this attack unilaterally. The sequencer processes the `LinkSigner` transaction as a normal fast-path transaction; no special privilege is required beyond possessing the linked signer's private key. The attack is a single transaction submitted through the normal sequencer flow.

---

### Recommendation

Pass `allowLinkedSigner = false` when validating `LinkSigner` transactions in `processTransactionImpl`. The authority to change the linked signer for a subaccount must be restricted exclusively to the subaccount owner (the address embedded in the `sender` bytes32). The slow-mode path already enforces this correctly via `validateSender`; the fast-path must match that invariant.

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // linked signer must NOT be allowed to re-link
);
```

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and links `botKey` as her linked signer via a slow-mode `LinkSigner` transaction. `linkedSigners[alice_subaccount] = botKey`.
2. Attacker compromises `botKey` (or is the bot operator acting maliciously).
3. Attacker uses `botKey` to sign a fast-path `LinkSigner` transaction: `{ sender: alice_subaccount, signer: attackerAddress, nonce: currentNonce }`.
4. Sequencer submits this transaction. `validateSignedTx(..., true)` recovers `botKey`, which equals `getLinkedSigner(alice_subaccount)` — signature accepted.
5. `linkedSigners[alice_subaccount]` is now set to `attackerAddress`.
6. Attacker signs a `WithdrawCollateral` transaction from `alice_subaccount` to drain all collateral. Signature recovers to `attackerAddress`, which is now the linked signer — accepted.
7. Alice's collateral is fully drained. [1](#0-0) [5](#0-4)

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
