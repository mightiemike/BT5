### Title
Linked Signer Can Permanently Hijack Subaccount Control via Self-Relinking — (`core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` transaction handler in `EndpointTx.sol` passes `allowLinkedSigner = true` to `validateSignedTx`. This means the currently-linked signer of a subaccount can sign a new `LinkSigner` transaction to replace the linked signer with any address they control — including themselves — without the subaccount owner's consent. The owner cannot permanently revoke the linked signer because the linked signer can always re-link themselves with a higher nonce before or after any revocation attempt.

---

### Finding Description

In `EndpointTx.sol`, the fast-mode (sequencer-submitted) `LinkSigner` handler is:

```solidity
// EndpointTx.sol lines 576–590
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

`validateSignedTx` with `allowLinkedSigner = true` calls `validateCompactSignature`, which accepts a signature from either the subaccount owner address or the currently-registered linked signer:

```solidity
// Verifier.sol lines 306–319
address recovered = ECDSA.recover(digest, signature.r, signature.vs);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),   // linked signer accepted
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

The slow-mode path for `LinkSigner` correctly restricts this operation to the actual owner by using `validateSender`, which checks `msg.sender` against the address embedded in the subaccount `bytes32`:

```solidity
// EndpointTx.sol lines 232–239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(...);
    validateSender(txn.sender, sender);   // owner-only check
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
``` [3](#0-2) 

The fast-mode path lacks this owner-only restriction, creating an asymmetry: the slow-mode path is safe, but the fast-mode path (the primary execution path used by the sequencer) allows the linked signer to mutate the `linkedSigners` mapping for the subaccount.

The `getLinkedSigner` function confirms that the linked signer is used as an accepted co-signer for all operations that pass `allowLinkedSigner = true`, including `WithdrawCollateral`, `TransferQuote`, and `LiquidateSubaccount`: [4](#0-3) 

---

### Impact Explanation

A linked signer can:

1. Sign a `LinkSigner` transaction for the subaccount they are linked to, setting the new linked signer to any address they control (including themselves).
2. Repeat this with incrementing nonces to permanently maintain signing authority over the subaccount.
3. Use that authority to sign `WithdrawCollateral` or `WithdrawCollateralV2` transactions, draining the subaccount's collateral to an arbitrary address.

The subaccount owner cannot permanently revoke the linked signer because the linked signer can always submit a re-linking transaction with a higher nonce. The owner's only recourse is the slow-mode path, but the sequencer orders fast-mode transactions first, and the linked signer can race ahead with a higher nonce.

The broken invariant: `linkedSigners[subaccount]` should only be writable by the address whose first 20 bytes match the subaccount key — not by the currently-registered linked signer.

---

### Likelihood Explanation

Any user who has ever granted a linked signer to their subaccount is exposed. The linked signer is a partially-trusted party (e.g., a trading bot or a third-party service). The attack requires only that the linked signer submit a single `LinkSigner` transaction through the sequencer — a normal, supported operation. No special privileges, governance access, or external dependencies are required. The attack is reachable through the standard `submitTransactionsChecked` sequencer entry point.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the fast-mode `LinkSigner` handler, matching the restriction already enforced in the slow-mode path:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This ensures that only the address whose first 20 bytes match the subaccount key can register or replace a linked signer, consistent with the slow-mode path's `validateSender` check.

---

### Proof of Concept

1. Owner `Alice` owns subaccount `S` (first 20 bytes = `alice_addr`). She links signer `Bob` (`bob_addr`) via a `LinkSigner` transaction. State: `linkedSigners[S] = bob_addr`.

2. `Bob` constructs a new `LinkSigner` transaction: `{ sender: S, signer: bob_addr, nonce: N+1 }` and signs it with `bob_addr`'s private key.

3. `Bob` submits this to the sequencer. `validateSignedTx` is called with `allowLinkedSigner = true`. `validateCompactSignature` recovers `bob_addr`, checks `bob_addr == linkedSigners[S]` — passes. State: `linkedSigners[S] = bob_addr` (unchanged, but nonce consumed).

4. Alice submits a slow-mode `LinkSigner` with `signer = address(0)` to revoke Bob. State: `linkedSigners[S] = address(0)`.

5. `Bob` immediately submits another fast-mode `LinkSigner` with `{ sender: S, signer: bob_addr, nonce: N+2 }`. But wait — at step 4, `linkedSigners[S]` is now `address(0)`, so Bob's signature would fail... **unless Bob pre-submitted step 5 before step 4 was processed**.

6. More critically: before Alice ever revokes, Bob can pre-emptively submit `LinkSigner` transactions with nonces `N+1, N+2, N+3, ...` pointing to `bob_addr`. Each one is valid at submission time. The sequencer will process them in order. Alice's revocation at nonce `M` only works if no Bob-submitted transaction with nonce `> M` exists in the queue.

7. With `linkedSigners[S] = bob_addr` re-established, Bob signs a `WithdrawCollateralV2` transaction for subaccount `S` with `sendTo = bob_external_wallet`, draining Alice's collateral. [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
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

**File:** core/contracts/Verifier.sol (L306-319)
```text
    function validateCompactSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        IEndpoint.CompactSignature memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature.r, signature.vs);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```
