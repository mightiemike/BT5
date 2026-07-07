### Title
`WithdrawCollateral` (V1) charges live `withdrawFeeX18` at sequencer processing time, not at user signing time — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateral` (V1) transaction type charges the withdrawal fee using the **current live** `spotEngine.getConfig(productId).withdrawFeeX18` at the moment the sequencer processes the transaction, not the value that existed when the user signed and submitted it. Because the V1 signed struct contains no fee commitment field, any change to `withdrawFeeX18` between signing and sequencer inclusion silently alters the fee deducted from the user's collateral balance.

---

### Finding Description

In `processTransactionImpl`, the `WithdrawCollateral` (V1) branch reads the withdrawal fee from the live spot engine config at execution time: [1](#0-0) 

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,  // live value
    signedTx.tx.productId
);
```

The `SignedWithdrawCollateral` struct that the user signs contains only `sender`, `productId`, `amount`, `nonce`, and `signature` — **no fee field**. The user therefore cannot commit to a specific fee at signing time.

Contrast this with `WithdrawCollateralV2`, which was introduced precisely to address this gap: it carries an explicit `feeX18` field in the signed payload and enforces `signedTx.feeX18 <= currentFeeX18` at processing time: [2](#0-1) 

The existence of V2 confirms the protocol team recognized the fee-commitment gap in V1. However, V1 remains active and reachable.

The root cause mirrors the reported bug class exactly:

| Original Bug | Nado Analog |
|---|---|
| `WITHDRAWAL_STAKE` received at submit, not stored per-request | `withdrawFeeX18` not committed in signed V1 struct |
| `notarizeSettlement()` returns current global value | `processTransactionImpl` charges current live config value |
| Admin calls `setWithdrawStake()` between submit and notarize | Admin updates `withdrawFeeX18` between user signing and sequencer inclusion |

---

### Impact Explanation

**Impact: Medium–High**

- If `withdrawFeeX18` **increases** between signing and processing: the user's collateral is debited by a larger fee than they agreed to. Their net withdrawal amount is silently reduced. This is an unexpected loss of funds for the user.
- If `withdrawFeeX18` **decreases**: the user pays less than the current rate (loss of protocol fee revenue).
- In a mass-withdrawal scenario where many V1 requests are queued and the fee is raised, all pending users suffer unexpected collateral loss simultaneously.
- A frontrun attack is possible: an attacker observes a pending V1 withdrawal in the sequencer mempool, triggers an admin fee increase (if they have that access), and the victim's withdrawal is processed at the higher fee.

---

### Likelihood Explanation

**Likelihood: Low**

- Requires `withdrawFeeX18` to be changed while V1 `WithdrawCollateral` transactions are pending in the sequencer queue.
- The sequencer typically processes transactions quickly, narrowing the window.
- However, during sequencer downtime or slow-mode fallback periods, the window widens significantly.
- The protocol's own introduction of V2 with fee commitment confirms this is a realistic operational concern.

---

### Recommendation

1. **Deprecate V1 `WithdrawCollateral`** in `processTransactionImpl` and require all new withdrawals to use V2, which already carries a user-committed `feeX18` field.
2. If V1 must remain supported, cap the charged fee at the fee value that was in effect at the `nSubmissions` index corresponding to the transaction's submission, or store the fee snapshot per queued withdrawal.
3. Alternatively, enforce that `withdrawFeeX18` cannot be changed while any V1 withdrawal transactions remain unprocessed in the sequencer queue.

---

### Proof of Concept

1. User signs a `WithdrawCollateral` (V1) transaction for product `P` when `withdrawFeeX18 = 1e15` ($0.001). The signed struct contains no fee field.
2. User submits the signed transaction to the sequencer off-chain.
3. Before the sequencer includes the transaction, the admin updates `withdrawFeeX18` for product `P` to `1e17` ($0.1) — a 100× increase.
4. The sequencer calls `submitTransactionsChecked(...)` → `processTransactionImpl(...)`.
5. At line 427 of `EndpointTx.sol`, `spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18` now returns `1e17`.
6. `chargeFee(signedTx.tx.sender, 1e17, productId)` is executed — the user's collateral is debited $0.1 instead of the $0.001 they expected when signing.
7. The user receives $0.099 less collateral than intended, with no on-chain mechanism to detect or prevent this at signing time. [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L437-458)
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
```
