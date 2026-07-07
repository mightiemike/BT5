### Title
Fast Withdrawal Fee Rounds to Zero for Low-Decimal Tokens Due to Decimal-Dependent Division — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`fastWithdrawalFeeAmount` in `BaseWithdrawPool` scales the fee into an X18 internal representation and then divides back by `10^(18 - decimals)` to recover native-decimal units. For tokens whose `decimals` are significantly less than 18 (including 0-decimal tokens, which the code explicitly permits), the final division truncates the fee to zero, allowing any fast-withdrawal provider to execute fee-free fast withdrawals.

---

### Finding Description

`fastWithdrawalFeeAmount` computes the fee as follows:

```solidity
// BaseWithdrawPool.sol L139-L148
uint8 decimals = token.decimals();
require(decimals <= MAX_DECIMALS);                          // MAX_DECIMALS = 18; 0 is allowed
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
int128 amountX18 = int128(amount) * int128(multiplier);

int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
// FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000 (0.1% in X18)
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);                        // ← truncating integer division
``` [1](#0-0) 

`MAX_DECIMALS = 18` and the guard is `decimals <= MAX_DECIMALS`, so `decimals = 0` is a valid, accepted value. [2](#0-1) 

For a 0-decimal token, `multiplier = 10^18`. The final line performs integer division `feeX18 / 10^18`. Because `feeX18` is an X18-scaled value, this division is correct in principle — but it truncates toward zero. Two concrete paths produce `fee = 0`:

**Path A — `minFeeX18` branch (small withdrawals):**
`minFeeX18 = 5 * withdrawFeeX18`. If `withdrawFeeX18 < 2e17` (i.e., the configured minimum withdrawal fee is less than $0.20 in X18 terms, which is typical), then `minFeeX18 < 10^18` and `minFeeX18 / 10^18 = 0`.

**Path B — proportional branch (any withdrawal < 1000 native units):**
`proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18) = 1e15 * amount`.
Return value = `1e15 * amount / 10^18 = amount / 1000`.
For `amount < 1000` (0-decimal tokens), this truncates to 0.

The caller of `submitFastWithdrawal` is an unprivileged fast-withdrawal provider. When `fee = 0`, the branch `safeTransferFrom(token, msg.sender, uint128(fee))` transfers nothing, and `fees[productId] += 0` records nothing. [3](#0-2) 

---

### Impact Explanation

The protocol permanently loses fast-withdrawal fee revenue for any spot product whose underlying token has decimals significantly below 18. A fast-withdrawal provider calling `submitFastWithdrawal` with a 0-decimal token pays zero fee regardless of the withdrawal size (for amounts < 1000 native units) or regardless of the configured `withdrawFeeX18` (when `minFeeX18` dominates). The `fees[productId]` accounting mapping is also corrupted — it accumulates 0 instead of the intended fee — so any downstream fee-claiming logic operates on incorrect state.

---

### Likelihood Explanation

The `require(decimals <= MAX_DECIMALS)` guard explicitly permits 0-decimal tokens. [4](#0-3) 

`submitFastWithdrawal` is a `public` function with no access control beyond signature verification; any address can call it once a valid signed withdrawal transaction exists. [5](#0-4) 

The trigger requires only that a 0-decimal (or very low-decimal) token be listed as a spot product — a configuration the contract code does not prevent. Once such a product exists, every fast withdrawal against it silently bypasses the fee.

---

### Recommendation

Remove the dependency on token decimals from the fee calculation entirely. Compute and compare fees exclusively in the X18 internal representation, and only convert to native units at the final transfer step using a ceiling division to avoid rounding to zero:

```solidity
int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
// Ceiling division: ensure at least 1 native unit when feeX18 > 0
int128 feeNative = (feeX18 + int128(multiplier) - 1) / int128(multiplier);
return feeNative;
```

Alternatively, enforce a minimum fee of 1 native unit whenever `feeX18 > 0`:

```solidity
int128 feeNative = feeX18 / int128(multiplier);
if (feeX18 > 0 && feeNative == 0) feeNative = 1;
return feeNative;
```

---

### Proof of Concept

**Setup:** A spot product is listed with a 0-decimal token (e.g., a wrapped integer-unit asset). `withdrawFeeX18 = 1e15` ($0.001).

**Trace through `fastWithdrawalFeeAmount` with `amount = 500`:**

1. `decimals = 0`
2. `multiplier = 10^(18 - 0) = 10^18`
3. `amountX18 = 500 * 10^18 = 5e20`
4. `proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(5e20) = (1e15 * 5e20) / 10^18 = 5e17`
5. `minFeeX18 = 5 * 1e15 = 5e15`
6. `feeX18 = max(5e17, 5e15) = 5e17`
7. `return 5e17 / 10^18 = 0` ← fee is zero

**In `submitFastWithdrawal`:** `fee = 0`, so `safeTransferFrom(token, msg.sender, 0)` is called — no fee is collected. The provider withdraws 500 tokens of a 0-decimal asset with zero cost. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-88)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;
```

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

**File:** core/contracts/common/Constants.sol (L17-25)
```text
int128 constant ONE = 10**18;

uint8 constant MAX_DECIMALS = 18;

int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00

int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```
