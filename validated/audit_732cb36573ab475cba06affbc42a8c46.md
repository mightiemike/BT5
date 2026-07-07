### Title
Zero-amount fee `safeTransferFrom` in `submitFastWithdrawal` can cause denial of service for fast withdrawal relayers — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary
When a third-party relayer submits a fast withdrawal on behalf of a user (`sendTo != msg.sender`), the protocol collects a fee via `safeTransferFrom`. The fee is computed via integer division and can round to zero for small withdrawal amounts on low-decimal tokens when `withdrawFeeX18` is zero. If the underlying token reverts on zero-value transfers, the `safeTransferFrom(token, msg.sender, 0)` call reverts, permanently blocking the relayer path for that withdrawal index.

---

### Finding Description

In `BaseWithdrawPool.submitFastWithdrawal`, the relayer branch unconditionally calls:

```solidity
safeTransferFrom(token, msg.sender, uint128(fee));
``` [1](#0-0) 

The fee is computed by `fastWithdrawalFeeAmount`:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
int128 amountX18 = int128(amount) * int128(multiplier);
int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;
int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);
``` [2](#0-1) 

The final division `feeX18 / int128(multiplier)` is integer division. For a 6-decimal token (e.g., USDC), `multiplier = 10^12`. If `withdrawFeeX18 = 0` for the product, then `minFeeX18 = 0`. For a small withdrawal amount where `proportionalFeeX18 < 10^12`, the returned fee is `0`. The subsequent `safeTransferFrom(token, msg.sender, 0)` call will revert on any ERC20 token that reverts on zero-value transfers.

The `ERC20Helper.safeTransferFrom` wrapper propagates the revert:

```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [3](#0-2) 

Because `markedIdxs[idx]` is set to `true` before the fee transfer attempt:

```solidity
markedIdxs[idx] = true;
...
safeTransferFrom(token, msg.sender, uint128(fee));
``` [4](#0-3) 

Wait — actually `markedIdxs[idx] = true` is set at line 88 before the fee transfer. If the call reverts, the state change is rolled back (Solidity reverts all state changes on revert). So the index is not permanently consumed. However, every relayer attempt for this withdrawal will hit the same zero-fee revert, permanently blocking the relayer path for this token/amount combination.

---

### Impact Explanation

Any fast withdrawal where the computed fee rounds to zero — due to a small withdrawal amount, a low-decimal token, and `withdrawFeeX18 = 0` — cannot be processed by a third-party relayer. The `submitFastWithdrawal` call reverts unconditionally for every relayer attempt. The user must fall back to the slow withdrawal path or submit the fast withdrawal themselves as `sendTo == msg.sender` (where the zero-fee case is handled correctly via subtraction). The fast withdrawal relayer mechanism is rendered non-functional for affected token/amount pairs.

---

### Likelihood Explanation

The protocol supports arbitrary spot tokens registered via `addEngine`. Any token with fewer than 18 decimals (e.g., 6-decimal stablecoins) combined with a product configured with `withdrawFeeX18 = 0` and a small withdrawal amount produces a zero fee. The condition is reachable by any unprivileged user who opens a position with such a token and requests a fast withdrawal of a small amount.

---

### Recommendation

Add a zero-check before the `safeTransferFrom` call in the relayer branch:

```diff
} else {
+   if (fee > 0) {
        safeTransferFrom(token, msg.sender, uint128(fee));
+   }
}
``` [5](#0-4) 

---

### Proof of Concept

1. Register a product backed by a 6-decimal ERC20 token that reverts on zero-value transfers, with `withdrawFeeX18 = 0`.
2. A user opens a position and signs a `WithdrawCollateral` transaction for a small amount (e.g., 1 unit = `10^6` raw, which is `10^12` in X18 format after `multiplier = 10^12`).
3. `proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(10^12)` — for any rate below `1e18` (i.e., below 100%), this is `< 10^12`.
4. `minFeeX18 = 5 * 0 = 0`.
5. `feeX18 / 10^12 = 0` (integer division rounds down).
6. A relayer calls `submitFastWithdrawal(idx, transaction, signatures)` with `sendTo != msg.sender`.
7. `safeTransferFrom(token, relayer, 0)` is called — the token reverts.
8. The fast withdrawal cannot be processed via the relayer path. [6](#0-5)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L134-149)
```text
    function fastWithdrawalFeeAmount(
        IERC20Base token,
        uint32 productId,
        uint128 amount
    ) public view returns (int128) {
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L36-41)
```text
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```
