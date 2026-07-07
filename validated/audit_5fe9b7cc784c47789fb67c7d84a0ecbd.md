### Title
User-Controlled `feeX18` in `WithdrawCollateralV2` Enables Complete Withdrawal Fee Bypass — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateralV2` processing path in `EndpointTx.processTransactionImpl` accepts a user-supplied `feeX18` field embedded in the signed transaction body. The on-chain validation only bounds this value to `[0, currentFeeX18]`, meaning any user can sign a withdrawal with `feeX18 = 0` and pay zero withdrawal fees. The sequencer, acting as a relay for user-signed transactions, submits whatever the user signed. The on-chain protocol never enforces a minimum fee, creating a direct fee bypass.

---

### Finding Description

In `processTransactionImpl`, the `WithdrawCollateralV2` branch decodes a `SignedWithdrawCollateralV2` struct and applies the following validation before charging fees:

```solidity
int128 currentFeeX18 = spotEngine
    .getConfig(signedTx.tx.productId)
    .withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(
    signedTx.tx.sender,
    signedTx.feeX18,          // ← user-controlled, can be 0
    signedTx.tx.productId
);
``` [1](#0-0) 

The `feeX18` field is part of the EIP-712 signed payload. `validateSignedTx` computes the digest over the entire `transaction[1:]` body, which includes `feeX18`:

```solidity
_hashTypedDataV4(
    computeDigest(
        IEndpoint.TransactionType(uint8(transaction[0])),
        transaction[1:]          // ← feeX18 is inside this
    )
)
``` [2](#0-1) 

This means the user explicitly signs `feeX18 = 0`. The sequencer, which relays user-signed transactions via `submitTransactions → processTransactionImpl`, submits the transaction as-is. The on-chain protocol accepts it because `0 >= 0` and `0 <= currentFeeX18`.

By contrast, the `WithdrawCollateral` (V1) path always charges the full configured fee with no user-supplied override:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
``` [3](#0-2) 

The V2 path introduces a user-controlled parameter (`feeX18`) that the on-chain protocol fails to bound away from zero, creating a fee bypass that V1 does not have.

---

### Impact Explanation

`chargeFee` debits the sender's spot balance and credits `sequencerFee[productId]`:

```solidity
function chargeFee(bytes32 sender, int128 fee, uint32 productId) internal {
    spotEngine.updateBalance(productId, sender, -fee);
    sequencerFee[productId] += fee;
}
``` [4](#0-3) 

With `feeX18 = 0`, neither the sender's balance is debited nor `sequencerFee` is incremented. The protocol's fee accounting is corrupted: `sequencerFee` underreports collected fees, and when `DumpFees` is processed, the sequencer receives less than it should. Any user who withdraws via V2 with `feeX18 = 0` extracts the full withdrawal amount without paying the protocol's configured withdrawal fee.

---

### Likelihood Explanation

The attack requires only that the user sign a `WithdrawCollateralV2` transaction with `feeX18 = 0` and submit it to the sequencer. The sequencer is a relay: it submits what users sign. There is no off-chain enforcement visible in the on-chain code, and the on-chain protocol imposes no minimum. Any user aware of the V2 struct layout can exploit this on every withdrawal, making the likelihood high for informed users.

---

### Recommendation

Enforce a minimum fee in the `WithdrawCollateralV2` path. The simplest fix is to require `feeX18 == currentFeeX18`, matching V1 behavior. If discounted fees are intentional, enforce a protocol-defined floor (e.g., `feeX18 >= minWithdrawFeeX18`) rather than allowing zero.

---

### Proof of Concept

1. User constructs a `WithdrawCollateralV2` transaction with `feeX18 = 0`, `productId = X`, `amount = A`, `sendTo = address(0)`.
2. User signs it via EIP-712 (digest covers `feeX18 = 0`).
3. User submits the signed transaction to the sequencer.
4. Sequencer calls `submitTransactions([transaction])` → `processTransactionImpl`.
5. `validateSignedTx` passes (valid signature over `feeX18 = 0`).
6. `require(0 >= 0)` passes; `require(0 <= currentFeeX18)` passes.
7. `chargeFee(sender, 0, productId)` — zero fee charged, `sequencerFee[productId]` unchanged.
8. `clearinghouse.withdrawCollateral(...)` executes the full withdrawal.
9. User receives full `amount` with no fee deducted; protocol fee accounting is corrupted. [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L94-104)
```text
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
