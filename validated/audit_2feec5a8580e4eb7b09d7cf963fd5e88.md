### Title
User-Controlled `feeX18` in `WithdrawCollateralV2` Allows Zero-Fee Withdrawal - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateralV2` transaction path in `EndpointTx.sol` allows the user to specify the withdrawal fee they are willing to pay via a user-controlled `feeX18` field. Because the only validation is that `feeX18 >= 0` and `feeX18 <= currentFeeX18`, a user can set `feeX18 = 0` and pay no withdrawal fee at all, while the sequencer still processes and settles the withdrawal.

---

### Finding Description

In `processTransactionImpl`, the `WithdrawCollateralV2` branch reads a user-signed `feeX18` value and charges it directly:

```solidity
// EndpointTx.sol lines 437–465
} else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
    IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
        transaction[1:],
        (IEndpoint.SignedWithdrawCollateralV2)
    );
    validateSignedTx(...);
    int128 currentFeeX18 = spotEngine
        .getConfig(signedTx.tx.productId)
        .withdrawFeeX18;
    require(signedTx.feeX18 >= 0);
    require(signedTx.feeX18 <= currentFeeX18);
    chargeFee(
        signedTx.tx.sender,
        signedTx.feeX18,          // <-- user-controlled, can be 0
        signedTx.tx.productId
    );
``` [1](#0-0) 

The two `require` checks only enforce that the fee is non-negative and does not exceed the configured maximum. They do not enforce a minimum. A user can therefore sign a `WithdrawCollateralV2` transaction with `feeX18 = 0` and pay nothing.

Contrast this with the `WithdrawCollateral` (V1) path, which always charges the full configured fee with no user input:

```solidity
// EndpointTx.sol lines 425–429
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
``` [2](#0-1) 

The `chargeFee` function credits the charged amount to `sequencerFee[productId]`, which is the sequencer's compensation pool. Setting `feeX18 = 0` means `sequencerFee` receives nothing for that withdrawal. [3](#0-2) 

---

### Impact Explanation

**Impact: Medium**

Every user who submits a `WithdrawCollateralV2` transaction can set `feeX18 = 0` and pay zero withdrawal fees. The sequencer still processes and settles the withdrawal (it has no on-chain mechanism to reject a validly signed transaction based on fee level), but receives no compensation. Over time, or at scale, this drains the sequencer's expected fee revenue and breaks the protocol's fee accounting invariant: `sequencerFee[productId]` accumulates less than the protocol intends, and the eventual `DumpFees` / `claimSequencerFees` call distributes less to the protocol than expected. [4](#0-3) 

---

### Likelihood Explanation

**Likelihood: High**

The `feeX18` field is part of the user-signed struct. Any user who is aware of the `WithdrawCollateralV2` transaction type can trivially set `feeX18 = 0` when constructing their signed payload. No special privileges, flash loans, or multi-step exploits are required. The V2 path was presumably introduced to give users flexibility in fee specification, but the missing minimum enforcement makes zero-fee withdrawals universally available to all users.

---

### Recommendation

Enforce a minimum fee equal to the configured `withdrawFeeX18` for the product, or remove the user-specified fee field entirely and always charge the full configured fee as the V1 path does. If partial fee discounts are intentional (e.g., for specific integrations), the minimum should be enforced at a non-zero floor and the discount logic should be gated by an access-controlled allowlist rather than open to all callers.

```solidity
require(signedTx.feeX18 >= currentFeeX18, "Fee below minimum");
```

Or, if discounts are desired for specific senders, gate the discount:

```solidity
if (!isDiscountEligible(signedTx.tx.sender)) {
    require(signedTx.feeX18 >= currentFeeX18, "Fee below minimum");
}
```

---

### Proof of Concept

1. Alice wants to withdraw collateral for `productId = 1`.
2. The configured `withdrawFeeX18` for product 1 is `1e15` (0.1% of 1e18).
3. Alice constructs a `WithdrawCollateralV2` transaction with `feeX18 = 0` and signs it.
4. Alice submits the signed transaction to the sequencer (e.g., via the off-chain API).
5. The sequencer calls `submitTransactionsChecked`, which calls `processTransactionImpl`.
6. The checks `require(signedTx.feeX18 >= 0)` and `require(signedTx.feeX18 <= currentFeeX18)` both pass.
7. `chargeFee(signedTx.tx.sender, 0, productId)` is called — zero is deducted from Alice's balance and zero is added to `sequencerFee[productId]`.
8. `clearinghouse.withdrawCollateral(...)` executes normally and Alice receives her full withdrawal amount.
9. Alice has paid no withdrawal fee. The sequencer's `sequencerFee` accumulation for this product is not incremented. [5](#0-4)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L244-253)
```text
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
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
