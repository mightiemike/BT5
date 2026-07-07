### Title
`WithdrawCollateralV2` Fee Validation Uses Current `withdrawFeeX18` Instead of Fee at Signing Time, Blocking Pending Withdrawals After Fee Decrease — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

When processing a `WithdrawCollateralV2` transaction, the protocol validates the user's signed `feeX18` against the **current** `withdrawFeeX18` from the spot engine config. Because `withdrawFeeX18` is a mutable parameter, a decrease in this value after a user has signed their withdrawal will cause the check to revert, permanently invalidating the signed transaction and blocking the user's withdrawal until they re-sign.

---

### Finding Description

In `processTransactionImpl`, the `WithdrawCollateralV2` branch reads the current fee from the spot engine config at execution time and enforces:

```solidity
int128 currentFeeX18 = spotEngine
    .getConfig(signedTx.tx.productId)
    .withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
``` [1](#0-0) 

The user signs the `WithdrawCollateralV2` struct off-chain, embedding a `feeX18` value that is valid (i.e., `<= withdrawFeeX18`) at the time of signing. The signed value is never re-validated against the fee that was in effect at signing time — only against the fee at execution time.

If the protocol admin decreases `withdrawFeeX18` for a product between the moment the user signs and the moment the sequencer submits the batch on-chain, the invariant `signedTx.feeX18 <= currentFeeX18` is violated, and the transaction reverts. The user's signed withdrawal is now invalid and cannot be executed without re-signing with a new, lower fee.

The `WithdrawCollateralV2` transaction type is processed exclusively in the fast-mode path (`processTransactionImpl`), which is called from `submitTransactionsChecked` without any try/catch:

```solidity
for (uint256 i = 0; i < transactions.length; i++) {
    bytes calldata transaction = transactions[i];
    processTransaction(transaction);
    nSubmissions += 1;
}
``` [2](#0-1) 

A revert in `processTransaction` propagates and reverts the entire sequencer batch, not just the single withdrawal.

---

### Impact Explanation

A user who has signed a `WithdrawCollateralV2` transaction with `feeX18 = F` (valid at signing time) will have their withdrawal permanently blocked if `withdrawFeeX18` is subsequently decreased to `F' < F`. The signed transaction cannot be executed; the user must re-sign with `feeX18 <= F'`. Additionally, if the stale transaction is included in a sequencer batch, the entire batch reverts, disrupting all other users' transactions in that batch.

---

### Likelihood Explanation

The likelihood is low but realistic. Protocol operators may legitimately decrease withdrawal fees as a competitive or governance action. Any user who signed a `WithdrawCollateralV2` transaction in the window between the fee decrease being decided and it being applied on-chain will be affected. The sequencer may not be aware of the fee change before submitting the batch.

---

### Recommendation

Cache the `withdrawFeeX18` value at the time the user signs the transaction by including it in the signed struct and validating against the cached value, or change the check to validate that `signedTx.feeX18 >= 0` only (accepting any non-negative fee the user agreed to pay, up to the current maximum). Alternatively, validate that `signedTx.feeX18 >= currentFeeX18` (user must agree to pay at least the current fee), which is the semantically correct direction for a fee floor check and avoids the regression when fees decrease.

---

### Proof of Concept

1. `withdrawFeeX18` for product `P` is `100` (in X18 units).
2. User signs `WithdrawCollateralV2` with `feeX18 = 100` and submits to the sequencer.
3. Admin decreases `withdrawFeeX18` for product `P` to `50`.
4. Sequencer submits the batch containing the user's signed withdrawal.
5. `processTransactionImpl` executes: `currentFeeX18 = 50`, `signedTx.feeX18 = 100`.
6. `require(100 <= 50)` → **REVERT**.
7. The entire sequencer batch reverts. The user's withdrawal is blocked and must be re-signed. [3](#0-2)

### Citations

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

**File:** core/contracts/Endpoint.sol (L289-293)
```text
        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
```
