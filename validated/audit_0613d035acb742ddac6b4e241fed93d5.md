### Title
Fast Withdrawal Fee Truncates to Zero for Low-Decimal Tokens, Enabling Fee-Free Fast Withdrawals — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`fastWithdrawalFeeAmount` in `BaseWithdrawPool.sol` computes the fee in X18 internal representation and then converts it back to native token decimals via integer division by `multiplier = 10^(18 - decimals)`. For tokens with low decimal counts (e.g., 2 decimals), this final division truncates to zero for withdrawal amounts below a threshold, allowing users to execute fast withdrawals with zero protocol fee.

---

### Finding Description

The fee computation in `fastWithdrawalFeeAmount` is:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
int128 amountX18 = int128(amount) * int128(multiplier);

int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);   // ← truncating integer division
``` [1](#0-0) 

`FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000`

### Citations

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
