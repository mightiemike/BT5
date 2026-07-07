### Title
Unsigned `feeX18` Field in `SignedWithdrawCollateralV2` Allows Sequencer to Charge Arbitrary Fees Without User Consent — (File: `core/contracts/Verifier.sol`, `core/contracts/EndpointTx.sol`)

---

### Summary

The `feeX18` field in `SignedWithdrawCollateralV2` is excluded from the EIP-712 digest computed by `Verifier.computeDigest`. A user's signature covers only the inner `WithdrawCollateralV2` struct fields. The sequencer can freely set `feeX18` to any value up to `currentFeeX18` when submitting the transaction, charging fees the user never agreed to and cannot limit through their signature.

---

### Finding Description

`IEndpoint.SignedWithdrawCollateralV2` is defined as:

```solidity
struct SignedWithdrawCollateralV2 {
    WithdrawCollateralV2 tx;
    CompactSignature signature;
    int128 feeX18;          // ← outside the signed inner struct
}
``` [1](#0-0) 

The EIP-712 type string and digest for this transaction type in `Verifier.computeDigest` covers only the inner struct fields:

```
WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,
                     uint64 nonce,address sendTo,uint128 appendix)
``` [2](#0-1) 

The digest computation encodes exactly those six fields and nothing else: [3](#0-2) 

In `EndpointTx.processTransactionImpl`, after the signature is validated against that digest, `feeX18` is read from the outer struct and used to deduct funds from the user's balance:

```solidity
int128 currentFeeX18 = spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [4](#0-3) 

The only constraint on `feeX18` is that it must be non-negative and at most `currentFeeX18`. Because `feeX18` is not part of the signed digest, the user's signature provides zero binding over the fee amount. The sequencer constructs the outer `SignedWithdrawCollateralV2` struct and can freely choose any `feeX18` in `[0, currentFeeX18]` without invalidating the user's signature.

This is the direct Nado analog of the reported vulnerability: a user signs a well-defined payload, but an execution-critical parameter (`feeX18`, like the original `req.callback`) is supplied by an external party and is not covered by the signature, allowing that party to act against the user's interests without their knowledge or consent.

---

### Impact Explanation

`chargeFee` reduces the user's spot balance for the withdrawal product by `feeX18`: [5](#0-4) 

A sequencer that sets `feeX18 = currentFeeX18` on every `WithdrawCollateralV2` transaction extracts the maximum configured fee from every user withdrawal. The user receives `amount - feeX18` worth of collateral instead of `amount`, with no on-chain mechanism to detect or prevent this. The corrupted state is the user's spot balance for `productId`, which is decremented by an amount the user never agreed to.

---

### Likelihood Explanation

The sequencer is the sole party that constructs and submits `WithdrawCollateralV2` transactions via `submitTransactionsChecked`. [6](#0-5) 

No compromise of keys or external systems is required. The sequencer operates within its normal, legitimate code path and can set `feeX18` to the maximum on every withdrawal without triggering any on-chain check. Users have no way to specify, cap, or verify the fee before their funds are deducted.

---

### Recommendation

**Short term:** Add `feeX18` (or a user-specified maximum acceptable fee) to the `WithdrawCollateralV2` EIP-712 type string and include it in the `computeDigest` encoding. This binds the user's signature to the exact fee they consent to pay.

**Long term:** Audit all `Signed*` outer structs for fields that affect fund flows but are excluded from the inner signed struct. Any execution-critical value that is not covered by the user's signature represents an implicit trust grant to the sequencer that should be made explicit and bounded by user consent.

---

### Proof of Concept

1. User creates `WithdrawCollateralV2 { sender, productId, amount=1000e18, nonce, sendTo=0, appendix }` and signs it with their key.
2. Sequencer receives the signed inner struct and wraps it: `SignedWithdrawCollateralV2 { tx: <above>, signature: <user sig>, feeX18: currentFeeX18 }`.
3. Sequencer submits via `submitTransactionsChecked`.
4. `processTransactionImpl` calls `validateSignedTx` — signature validates correctly because `feeX18` is not in the digest.
5. `chargeFee(sender, currentFeeX18, productId)` deducts `currentFeeX18` from the user's balance.
6. `clearinghouse.withdrawCollateral(sender, productId, 1000e18, address(0), nSubmissions)` executes the withdrawal for the full `amount`.
7. Net result: user loses `currentFeeX18` in collateral beyond what they agreed to, with no recourse. [7](#0-6)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L106-110)
```text
    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```

**File:** core/contracts/Verifier.sol (L24-25)
```text
    string internal constant WITHDRAW_COLLATERAL_V2_SIGNATURE =
        "WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,uint64 nonce,address sendTo,uint128 appendix)";
```

**File:** core/contracts/Verifier.sol (L362-372)
```text
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.productId,
                    signedTx.tx.amount,
                    signedTx.tx.nonce,
                    signedTx.tx.sendTo,
                    signedTx.tx.appendix
                )
            );
```

**File:** core/contracts/EndpointTx.sol (L134-141)
```text
    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
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

**File:** core/contracts/Endpoint.sol (L278-293)
```text
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
```
