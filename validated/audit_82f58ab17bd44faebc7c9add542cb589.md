### Title
`WithdrawCollateralV2` Fee Bypass via Unsigned `feeX18` Field — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateralV2` transaction path allows any user to withdraw collateral while paying **zero withdrawal fee** by setting the `feeX18` field to `0` in the submitted transaction. This field is accepted and enforced by the contract but is **never committed to in the EIP-712 signature digest**, meaning the user's valid signature does not bind them to any particular fee amount.

---

### Finding Description

`SignedWithdrawCollateralV2` is defined with an outer `feeX18` field that sits outside the signed `tx` struct:

```solidity
struct SignedWithdrawCollateralV2 {
    WithdrawCollateralV2 tx;
    CompactSignature signature;
    int128 feeX18;          // <-- outside the signed tx struct
}
``` [1](#0-0) 

The EIP-712 digest computed in `Verifier.computeDigest` covers only the inner `WithdrawCollateralV2` fields (`sender`, `productId`, `amount`, `nonce`, `sendTo`, `appendix`). The `feeX18` field is entirely absent from the digest:

```solidity
digest = keccak256(
    abi.encode(
        keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.productId,
        signedTx.tx.amount,
        signedTx.tx.nonce,
        signedTx.tx.sendTo,
        signedTx.tx.appendix   // feeX18 is NOT here
    )
);
``` [2](#0-1) 

In `EndpointTx.processTransactionImpl`, the fee enforcement reads:

```solidity
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [3](#0-2) 

Because `feeX18` is not part of the signed digest, a user can submit any valid `WithdrawCollateralV2` signature with `feeX18 = 0`. Both `require` checks pass (`0 >= 0` and `0 <= currentFeeX18`), and `chargeFee` deducts nothing from the subaccount balance.

Contrast this with the `WithdrawCollateral` (V1) path, which correctly reads the fee directly from the protocol config and does not allow user input:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,  // protocol-controlled
    signedTx.tx.productId
);
``` [4](#0-3) 

---

### Impact Explanation

Every user who uses the `WithdrawCollateralV2` path can withdraw their full collateral balance while paying zero withdrawal fee. The `sequencerFee` accounting is never incremented for these withdrawals, permanently depriving the protocol of fee revenue on all V2 withdrawals. The `chargeFee` function debits the subaccount balance and credits `sequencerFee[productId]`; with `fee = 0`, neither side of this accounting entry is touched. [5](#0-4) 

---

### Likelihood Explanation

Likelihood is **high**. The entry path is a standard public function (`submitSlowModeTransaction`) callable by any user. No special role, privileged key, or external dependency is required. The user simply constructs a valid `WithdrawCollateralV2` transaction (signing only the `tx` struct as normal) and sets `feeX18 = 0` in the ABI-encoded bytes before submission. Any technically aware user or front-end that constructs raw transactions can trivially exploit this. [6](#0-5) 

---

### Recommendation

Include `feeX18` in the EIP-712 digest for `WithdrawCollateralV2` so that the user's signature commits to the exact fee they agreed to pay:

```solidity
digest = keccak256(
    abi.encode(
        keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.productId,
        signedTx.tx.amount,
        signedTx.tx.nonce,
        signedTx.tx.sendTo,
        signedTx.tx.appendix,
        signedTx.feeX18        // ADD THIS
    )
);
```

Alternatively, mirror the V1 approach: remove the user-supplied `feeX18` field entirely and read the fee directly from `spotEngine.getConfig(productId).withdrawFeeX18` at execution time, as is done for `WithdrawCollateral`. [7](#0-6) 

---

### Proof of Concept

1. User has a subaccount with collateral deposited for `productId = 1`.
2. User signs a `WithdrawCollateralV2` transaction covering `(sender, productId=1, amount=1000e6, nonce, sendTo=0x..., appendix=0)`. The signature is valid.
3. User ABI-encodes `SignedWithdrawCollateralV2` with `feeX18 = 0` (instead of the protocol's configured fee, e.g. `1e15`).
4. User calls `Endpoint.submitSlowModeTransaction(encodedTx)`. The slow-mode fee is paid in quote token at submission.
5. After the delay, the sequencer (or user via `executeSlowModeTransaction`) processes the transaction.
6. `validateSignedTx` succeeds — the digest does not include `feeX18`, so the signature is valid.
7. `require(0 >= 0)` passes. `require(0 <= 1e15)` passes.
8. `chargeFee(sender, 0, 1)` executes: `spotEngine.updateBalance(1, sender, 0)` — no fee deducted.
9. `clearinghouse.withdrawCollateral(...)` transfers the full `1000e6` to `sendTo`.
10. The user receives the full withdrawal amount with zero withdrawal fee charged. [8](#0-7)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L106-110)
```text
    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```

**File:** core/contracts/Verifier.sol (L357-373)
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
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
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

**File:** core/contracts/EndpointTx.sol (L425-429)
```text
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
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

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```
