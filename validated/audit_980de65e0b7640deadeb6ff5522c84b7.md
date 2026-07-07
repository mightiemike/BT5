### Title
`SignedWithdrawCollateralV2.feeX18` Is Not Included in the Signed Digest, Allowing the Sequencer to Overcharge Withdrawal Fees - (File: `core/contracts/Verifier.sol`, `core/contracts/EndpointTx.sol`)

---

### Summary

`WithdrawCollateralV2` introduces a user-specified `feeX18` field that is charged directly from the user's balance. However, `feeX18` is never included in the EIP-712 digest that the user signs. The sequencer can freely set `feeX18` to any value up to `currentFeeX18` without invalidating the user's signature, overcharging the user on every withdrawal.

---

### Finding Description

`Verifier.computeDigest()` handles `WithdrawCollateralV2` by hashing only the inner `tx` struct fields:

```
WithdrawCollateralV2(bytes32 sender, uint32 productId, uint128 amount,
                     uint64 nonce, address sendTo, uint128 appendix)
``` [1](#0-0) [2](#0-1) 

The digest computation encodes exactly those six fields and nothing else:

```solidity
digest = keccak256(
    abi.encode(
        keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.productId,
        signedTx.tx.amount,
        signedTx.tx.nonce,
        signedTx.tx.sendTo,
        signedTx.tx.appendix   // <-- feeX18 is absent
    )
);
``` [3](#0-2) 

Yet in `EndpointTx.processTransactionImpl()`, `signedTx.feeX18` — a field that lives **outside** the signed `tx` struct — is used to deduct a fee from the user's balance:

```solidity
int128 currentFeeX18 = spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [4](#0-3) 

`chargeFee` reduces the user's spot balance and credits `sequencerFee[productId]`:

```solidity
function chargeFee(bytes32 sender, int128 fee, uint32 productId) internal {
    spotEngine.updateBalance(productId, sender, -fee);
    sequencerFee[productId] += fee;
}
``` [5](#0-4) 

Because `feeX18` is not committed to in the user's signature, the sequencer can decode the user's signed transaction, replace `feeX18` with `currentFeeX18` (the maximum allowed value), re-encode the transaction, and submit it. The signature check passes because the digest does not cover `feeX18`.

The contrast with `WithdrawCollateral` (V1) makes the design intent clear: V1 always charges the live `currentFeeX18` with no user input. V2 was introduced precisely to let users commit to a specific fee at signing time — but the commitment is never enforced cryptographically. [6](#0-5) 

---

### Impact Explanation

Every `WithdrawCollateralV2` transaction can be silently upgraded by the sequencer to charge the maximum protocol withdrawal fee (`currentFeeX18`) regardless of what `feeX18` value the user intended. The excess fee accumulates in `sequencerFee[productId]` and is later claimed by the sequencer via `DumpFees`. This is a direct, quantifiable asset loss from every user who submits a `WithdrawCollateralV2` transaction expecting a lower fee.

---

### Likelihood Explanation

The sequencer processes every `WithdrawCollateralV2` transaction before it reaches the chain. No special access beyond normal sequencer operation is required to exploit this — the sequencer simply sets `feeX18 = currentFeeX18` in every batch it submits. The user has no on-chain mechanism to detect or prevent this substitution.

---

### Recommendation

Include `feeX18` in the EIP-712 type string and digest computation for `WithdrawCollateralV2`:

```solidity
string internal constant WITHDRAW_COLLATERAL_V2_SIGNATURE =
    "WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,"
    "uint64 nonce,address sendTo,uint128 appendix,int128 feeX18)";
```

And add `signedTx.feeX18` to the `abi.encode(...)` call in `Verifier.computeDigest()`. This mirrors the fix recommended in the reference report: include the manipulable value in the signed data so the contract can verify it matches what the user authorised. [1](#0-0) 

---

### Proof of Concept

1. User constructs `SignedWithdrawCollateralV2` with `feeX18 = 0` (zero fee), signs the inner `tx` struct, and submits to the sequencer off-chain.
2. Sequencer receives the signed transaction. It decodes the `abi`-encoded payload, replaces `feeX18` with `currentFeeX18` (e.g. `5e14` — 0.05%), and re-encodes.
3. Sequencer submits the modified transaction in a `submitTransactionsChecked` batch.
4. `validateSignedTx` recomputes the digest over `(sender, productId, amount, nonce, sendTo, appendix)` — identical to what the user signed — and the signature validates successfully.
5. `chargeFee(signedTx.tx.sender, currentFeeX18, productId)` deducts the maximum fee from the user's balance.
6. The user receives their withdrawal but has been charged the maximum fee instead of zero. [7](#0-6) [2](#0-1)

### Citations

**File:** core/contracts/Verifier.sol (L24-25)
```text
    string internal constant WITHDRAW_COLLATERAL_V2_SIGNATURE =
        "WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,uint64 nonce,address sendTo,uint128 appendix)";
```

**File:** core/contracts/Verifier.sol (L357-372)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedWithdrawCollateralV2)
            );
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
