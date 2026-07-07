### Title
Fast Withdrawal Fee Truncates to Zero for Low-Decimal Tokens — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`fastWithdrawalFeeAmount` in `BaseWithdrawPool.sol` scales the fee up to X18 precision, computes it, then divides back down by `multiplier = 10^(18 - decimals)`. For tokens with few decimals (e.g., 2), this final integer division truncates the fee to zero for small withdrawal amounts, allowing any caller to execute a fast withdrawal and pay no fee at all.

---

### Finding Description

`fastWithdrawalFeeAmount` computes the fast withdrawal fee as follows:

```solidity
// BaseWithdrawPool.sol lines 139–148
uint8 decimals = token.decimals();
require(decimals <= MAX_DECIMALS);                          // MAX_DECIMALS = 18
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
int128 amountX18 = int128(amount) * int128(multiplier);

int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
// FAST_WITHDRAWAL_FEE_RATE = 10^15 (0.1%)

int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;
int128 feeX18    = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);                        // ← truncation
```

For a 2-decimal token (e.g., EURS):

- `multiplier = 10^(18−2) = 10^16`
- `amountX18 = amount × 10^16`
- `proportionalFeeX18 = (10^15 × amount × 10^16) / 10^18 = amount × 10^13` (MathSD21x18 fixed-point multiply divides by `10^18`)

The returned fee is `feeX18 / 10^16`. This truncates to **zero** whenever `feeX18 < 10^16`, i.e., whenever `amount < 1000` raw units = **9.99 EURS**.

The `minFeeX18` guard (`5 × withdrawFeeX18`) only prevents this if `withdrawFeeX18 ≥ 2×10^15` (i.e., the per-product config is set to at least $0.002 in X18 terms). If `withdrawFeeX18` is zero or very small — a realistic default — `minFeeX18 = 0` and the proportional path alone governs, which truncates to zero.

The caller then proceeds through `submitFastWithdrawal`:

```solidity
// BaseWithdrawPool.sol lines 102–113
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);          // subtracts 0
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));  // transfers 0
}
fees[productId] += fee;                                 // accumulates 0
handleWithdrawTransfer(token, sendTo, transferAmount);  // full amount sent
```

No fee is deducted or collected; the user receives the full withdrawal amount.

---

### Impact Explanation

Any user submitting a fast withdrawal of a low-decimal token in a small-enough amount pays **zero** fast withdrawal fee. The protocol's `fees[productId]` mapping accumulates nothing for these transactions. The 0.1% fast withdrawal premium — the economic mechanism that compensates liquidity providers for the speed guarantee — is entirely bypassed. Repeated small fast withdrawals of a 2-decimal token (each < 10 EURS) drain the withdraw pool's liquidity at no cost to the withdrawer.

---

### Likelihood Explanation

The trigger requires only:
1. A supported spot product whose underlying token has ≤ 2 decimals (EURS is a real-world example with 2 decimals).
2. A withdrawal amount below the truncation threshold (< 1000 raw units = 9.99 EURS for a 2-decimal token).
3. A valid signed `WithdrawCollateral` or `WithdrawCollateralV2` transaction — obtainable by any user through the normal protocol flow.

No privileged access, no admin compromise, and no special conditions are required. The threshold scales with decimals: a 6-decimal token would require `amount < 10^5` raw units (< $0.10), which is also realistic for dust withdrawals.

---

### Recommendation

Two complementary fixes:

1. **Enforce a minimum fee in native token units.** After computing `feeX18 / multiplier`, require the result is at least 1 (one smallest unit of the token). If it rounds to zero, either revert or clamp to 1:
   ```solidity
   int128 fee = feeX18 / int128(multiplier);
   if (fee == 0) fee = 1;  // enforce minimum of 1 native unit
   return fee;
   ```

2. **Enforce a non-zero `withdrawFeeX18` in product config.** When registering a product with low decimals, require `withdrawFeeX18` to be large enough that `5 × withdrawFeeX18 / multiplier ≥ 1`. This ensures the `minFeeX18` guard always produces a non-zero native-unit fee.

---

### Proof of Concept

**Setup:** EURS token, 2 decimals, `withdrawFeeX18 = 0`.

**Calculation:**
- `multiplier = 10^16`
- Withdrawal `amount = 500` (= 5.00 EURS)
- `amountX18 = 500 × 10^16 = 5 × 10^18`
- `proportionalFeeX18 = (10^15 × 5×10^18) / 10^18 = 5 × 10^15`
- `minFeeX18 = 5 × 0 = 0`
- `feeX18 = max(5×10^15, 0) = 5×10^15`
- **Returned fee = `5×10^15 / 10^16 = 0`** (integer division truncates)

**Result:** `submitFastWithdrawal` collects zero fee; `fees[productId]` is unchanged; the user receives 5.00 EURS in full with no fast withdrawal premium paid. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L102-113)
```text
        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
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

**File:** core/contracts/common/Constants.sol (L19-25)
```text
uint8 constant MAX_DECIMALS = 18;

int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00

int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L23-31)
```text
    struct Config {
        address token;
        int128 interestInflectionUtilX18;
        int128 interestFloorX18;
        int128 interestSmallCapX18;
        int128 interestLargeCapX18;
        int128 withdrawFeeX18;
        int128 minDepositRateX18;
    }
```
