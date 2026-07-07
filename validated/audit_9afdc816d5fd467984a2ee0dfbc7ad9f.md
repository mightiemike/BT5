### Title
User Can Bypass Withdrawal Fee in `WithdrawCollateralV2` by Setting `feeX18 = 0` — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateralV2` transaction type allows the user to supply their own `feeX18` value in the signed transaction. The on-chain code only enforces `feeX18 >= 0` and `feeX18 <= currentFeeX18`, with no minimum floor. A user can sign a withdrawal with `feeX18 = 0` and pay zero withdrawal fee, while the V1 path always charges the full configured `withdrawFeeX18`.

---

### Finding Description

In `EndpointTx.sol`, the `WithdrawCollateral` (V1) handler unconditionally charges the full protocol-configured fee:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
``` [1](#0-0) 

The `WithdrawCollateralV2` handler, by contrast, reads `feeX18` directly from the user-signed transaction struct and applies only a ceiling check, with no floor:

```solidity
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
``` [2](#0-1) 

The `feeX18` field is part of `SignedWithdrawCollateralV2`, which is the full transaction body passed to `validateSignedTx` and hashed for signature verification: [3](#0-2) 

Because `feeX18` is included in the signed transaction body, the user explicitly controls it at signing time. Setting `feeX18 = 0` produces a valid signature over a zero-fee withdrawal. The on-chain code has no invariant that enforces `feeX18 == currentFeeX18` or any non-zero minimum.

The `chargeFee` function simply deducts the supplied amount from the sender's balance and credits `sequencerFee`: [4](#0-3) 

With `feeX18 = 0`, neither deduction nor credit occurs, and the full `amount` is still withdrawn via `clearinghouse.withdrawCollateral`.

---

### Impact Explanation

Any user can withdraw collateral via `WithdrawCollateralV2` while paying zero withdrawal fee. The protocol's `withdrawFeeX18` configuration is rendered unenforceable for V2 withdrawals. Protocol fee revenue from withdrawals is entirely lost for users who opt into the V2 path with `feeX18 = 0`. This is a direct accounting corruption: the fee is configured and expected to be collected, but the on-chain code does not enforce it.

---

### Likelihood Explanation

The `WithdrawCollateralV2` transaction type is a first-class supported path processed by the sequencer via `processTransactionImpl`. Any user who constructs and signs a `WithdrawCollateralV2` transaction with `feeX18 = 0` produces a fully valid signed transaction. The sequencer may apply off-chain validation, but the on-chain code provides no backstop. Since the V1 path enforces the fee and V2 does not, any user aware of the V2 path has a direct, repeatable mechanism to avoid fees on every withdrawal.

---

### Recommendation

Enforce that `feeX18` equals the current configured fee, mirroring the V1 behavior:

```solidity
require(signedTx.feeX18 == currentFeeX18, ERR_INVALID_FEE);
```

Alternatively, if partial fees are intentionally supported (e.g., for fee negotiation), enforce a non-zero minimum:

```solidity
require(signedTx.feeX18 > 0, ERR_INVALID_FEE);
```

---

### Proof of Concept

1. User constructs a `WithdrawCollateralV2` transaction with `feeX18 = 0` and signs it with their private key.
2. The signed transaction is submitted to the sequencer, which calls `submitTransactionsChecked` → `processTransactionImpl`.
3. On-chain: `validateSignedTx` verifies the signature (which covers `feeX18 = 0`) — passes.
4. `require(signedTx.feeX18 >= 0)` — passes (0 ≥ 0).
5. `require(signedTx.feeX18 <= currentFeeX18)` — passes (0 ≤ fee).
6. `chargeFee(sender, 0, productId)` — no fee deducted, no sequencer fee credited.
7. `clearinghouse.withdrawCollateral(sender, productId, amount, sendTo, idx)` — full `amount` withdrawn.
8. User receives full collateral with zero fee paid, while the V1 path would have charged `withdrawFeeX18`.

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

**File:** core/contracts/EndpointTx.sol (L425-429)
```text
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
```

**File:** core/contracts/EndpointTx.sol (L449-465)
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
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L97-110)
```text
    struct WithdrawCollateralV2 {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
        address sendTo;
        uint128 appendix; // Reserved for forward-compatible withdrawal features.
    }

    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```
