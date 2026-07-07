### Title
Outdated `withdrawFeeX18` Charged on `WithdrawCollateral` V1 If Fee Changes Between Signing and Execution — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateral` (V1) transaction handler reads `withdrawFeeX18` live from `spotEngine.getConfig()` at sequencer execution time rather than using a fee value committed to by the user at signing time. If the admin updates the withdrawal fee between when the user signs and when the sequencer processes the transaction, the user is charged a different — potentially higher — fee than they consented to.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `WithdrawCollateral` V1 branch charges the fee as follows:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
``` [1](#0-0) 

The user's signed `WithdrawCollateral` struct contains no fee field — it only carries `sender`, `productId`, `amount`, and `nonce`. The fee is therefore entirely determined by the current on-chain state of `spotEngine` at the moment the sequencer submits the batch, not at the moment the user signed. [2](#0-1) 

The protocol itself acknowledges this design flaw: `WithdrawCollateralV2` was introduced specifically to let the user commit to a `feeX18` inside the signed payload, with the contract enforcing `signedTx.feeX18 <= currentFeeX18`:

```solidity
int128 currentFeeX18 = spotEngine
    .getConfig(signedTx.tx.productId)
    .withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [3](#0-2) 

V1 remains live and reachable in `processTransactionImpl` with no such protection.

---

### Impact Explanation

A user who signs a `WithdrawCollateral` V1 transaction expecting to pay fee `F` can instead be charged fee `F'` (where `F' > F`) if the admin updates `withdrawFeeX18` before the sequencer processes the batch. The delta `F' - F` is silently deducted from the user's subaccount balance via `spotEngine.updateBalance`, constituting an unexpected and unconsented asset loss. [4](#0-3) 

---

### Likelihood Explanation

The sequencer typically processes transactions quickly, but the window between user signing and on-chain settlement is non-zero and protocol-defined. The admin can update product configs (including `withdrawFeeX18`) at any time through the sequencer's slow-mode or admin transaction paths. Any fee update that lands in this window silently changes the cost of a pending withdrawal without the user's knowledge or ability to cancel.

---

### Recommendation

Deprecate `WithdrawCollateral` V1 in favor of V2 exclusively, or backport the V2 protection into V1 by adding a `maxFeeX18` field to the `WithdrawCollateral` struct that the user signs and the contract enforces as an upper bound before charging.

---

### Proof of Concept

1. Admin sets `withdrawFeeX18 = 100` for product X via a config update.
2. User signs a `WithdrawCollateral` V1 transaction for product X, expecting to pay 100.
3. Admin updates `withdrawFeeX18 = 500` for product X (e.g., in response to market conditions).
4. Sequencer submits the user's previously signed transaction in the next batch.
5. `processTransactionImpl` executes: `spotEngine.getConfig(productId).withdrawFeeX18` now returns 500.
6. `chargeFee(sender, 500, productId)` is called — the user is charged 500 instead of 100.
7. The user loses 400 more than they agreed to, with no on-chain mechanism to prevent or revert it. [2](#0-1)

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

**File:** core/contracts/EndpointTx.sol (L449-458)
```text
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
```
