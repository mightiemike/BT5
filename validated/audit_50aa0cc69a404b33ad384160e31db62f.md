### Title
User-Controlled `feeX18` in `WithdrawCollateralV2` Allows Fee Bypass via Slow Mode — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`WithdrawCollateralV2` introduces a `feeX18` field in `SignedWithdrawCollateralV2` that is **not included in the user's EIP-712 signed digest**. When a user submits a `WithdrawCollateralV2` transaction through the slow mode queue, they control the raw transaction bytes — including `feeX18` — and can set it to `0`. The on-chain check only enforces `feeX18 <= currentFeeX18`, so `feeX18 = 0` passes. The user is then charged zero withdrawal fee, bypassing the configured `withdrawFeeX18` entirely.

---

### Finding Description

`SignedWithdrawCollateralV2` is defined as:

```solidity
struct SignedWithdrawCollateralV2 {
    WithdrawCollateralV2 tx;
    CompactSignature signature;
    int128 feeX18;   // ← outside the signed struct
}
``` [1](#0-0) 

The EIP-712 digest computed in `Verifier.sol` covers only the inner `WithdrawCollateralV2` struct fields (`sender`, `productId`, `amount`, `nonce`, `sendTo`, `appendix`) — `feeX18` is deliberately excluded:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.productId,
    signedTx.tx.amount,
    signedTx.tx.nonce,
    signedTx.tx.sendTo,
    signedTx.tx.appendix   // feeX18 is NOT here
));
``` [2](#0-1) 

In `processTransactionImpl`, the fee charged is exactly `signedTx.feeX18`, gated only by `<= currentFeeX18`:

```solidity
int128 currentFeeX18 = spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [3](#0-2) 

The slow mode queue accepts any non-owner transaction type (including `WithdrawCollateralV2`) from any user, charging only the flat `SLOW_MODE_FEE` ($1 USDC):

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [4](#0-3) 

Because the user submits the raw ABI-encoded `SignedWithdrawCollateralV2` bytes (including `feeX18`), and `feeX18` is not covered by the signature, the user can craft bytes with `feeX18 = 0`. The signature remains valid, the `<= currentFeeX18` check passes, and `chargeFee` is called with `0`.

---

### Impact Explanation

The user pays `0` in withdrawal fees (credited to `sequencerFee[productId]`) instead of the configured `withdrawFeeX18`. The sequencer's fee revenue from `WithdrawCollateralV2` withdrawals is eliminated for any user who routes through slow mode. This is a direct accounting corruption: `sequencerFee[productId]` receives `0` instead of `currentFeeX18` per bypassed withdrawal. [5](#0-4) 

---

### Likelihood Explanation

Any user who wishes to avoid the withdrawal fee can do so permissionlessly by submitting through slow mode with `feeX18 = 0`. The only cost is the $1 USDC slow mode fee and a 3-day delay. For any product whose `withdrawFeeX18` exceeds $1 (or for users making repeated withdrawals), this is economically rational. No special privileges, leaked keys, or admin access are required. [6](#0-5) 

---

### Recommendation

Include `feeX18` in the EIP-712 signed digest so the user commits to a specific fee at signing time, preventing post-signature manipulation. Alternatively, ignore `signedTx.feeX18` entirely when the transaction arrives via slow mode and always charge `currentFeeX18` directly — mirroring the `WithdrawCollateral` (V1) behavior:

```solidity
chargeFee(signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId);
``` [7](#0-6) 

---

### Proof of Concept

1. User constructs a `WithdrawCollateralV2` struct and signs it (EIP-712 over `sender`, `productId`, `amount`, `nonce`, `sendTo`, `appendix`).
2. User ABI-encodes `SignedWithdrawCollateralV2` with the valid signature and **`feeX18 = 0`**.
3. User calls `submitSlowModeTransaction` on `Endpoint`, paying only the $1 USDC slow mode fee.
4. After the 3-day delay, the sequencer (or anyone) calls `executeSlowModeTransaction`, which invokes `processTransactionImpl` with the user-supplied bytes.
5. `processTransactionImpl` decodes `feeX18 = 0`, passes `require(0 <= currentFeeX18)`, and calls `chargeFee(sender, 0, productId)`.
6. The user's collateral is withdrawn with zero withdrawal fee charged; `sequencerFee[productId]` is not incremented.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L106-110)
```text
    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
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

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
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

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```
