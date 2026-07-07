### Title
Missing `INT128_MAX` Bounds Check in `fastWithdrawalFeeAmount` Allows Fee Accounting Corruption — (`File: core/contracts/BaseWithdrawPool.sol`)

### Summary

`fastWithdrawalFeeAmount` in `BaseWithdrawPool.sol` casts a `uint128 amount` parameter directly to `int128` without a bounds check. In Solidity 0.8.x, explicit type casts do **not** revert on overflow — only arithmetic operators do. If `amount > INT128_MAX`, the cast silently wraps to a negative value, corrupting the fee computation and causing the function to return only the minimum fee regardless of the actual withdrawal size. Every analogous function in the same codebase (`depositCollateral`, `withdrawCollateral`) includes the missing guard.

---

### Finding Description

`fastWithdrawalFeeAmount` computes the fee for a fast withdrawal:

```solidity
// BaseWithdrawPool.sol lines 138–148
function fastWithdrawalFeeAmount(
    IERC20Base token,
    uint32 productId,
    uint128 amount
) public view returns (int128) {
    uint8 decimals = token.decimals();
    require(decimals <= MAX_DECIMALS);
    int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
    int128 amountX18 = int128(amount) * int128(multiplier);   // ← unsafe cast

    int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
    int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

    int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
    return feeX18 / int128(multiplier);
}
``` [1](#0-0) 

At line 142, `int128(amount)` silently wraps to a negative value when `amount > INT128_MAX` (i.e., `amount ≥ 2^127`). This makes `amountX18` negative, which makes `proportionalFeeX18` negative. `MathHelper.max(proportionalFeeX18, minFeeX18)` then returns only `minFeeX18` — the flat minimum fee — regardless of the actual withdrawal size.

By contrast, both `depositCollateral` and `withdrawCollateral` in `Clearinghouse.sol` include the explicit guard that is absent here:

```solidity
// Clearinghouse.sol line 199
require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
``` [2](#0-1) 

```solidity
// Clearinghouse.sol line 399
require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
``` [3](#0-2) 

The missing check is a broken invariant: the protocol assumes `amountX18` is a positive, correctly scaled representation of the withdrawal amount, but this assumption is violated for any `amount > INT128_MAX`.

---

### Impact Explanation

A user who can submit a fast withdrawal request with `amount > INT128_MAX` pays only the flat minimum fee (`minFeeX18`) instead of the proportional fee (`FAST_WITHDRAWAL_FEE_RATE × amount`). For large withdrawals this is a near-total fee bypass. The corrupted return value of `fastWithdrawalFeeAmount` is used by the WithdrawPool to determine how much to deduct from the user, so the accounting delta applied to the subaccount is wrong: the protocol collects far less fee than it should, directly reducing protocol revenue and violating the fee-accounting invariant.

---

### Likelihood Explanation

The `fastWithdrawalFeeAmount` function is `public view` and its result feeds the fast-withdrawal execution path. The trigger requires `amount > INT128_MAX` (`≥ 2^127 ≈ 1.7 × 10^38`). Whether a user can supply such a value depends on the WithdrawPool caller — if the caller forwards a user-supplied `uint128` without its own bounds check, the path is reachable. The absence of the guard in `fastWithdrawalFeeAmount` itself (while present in every other collateral-handling function) means the function provides no defense-in-depth. Likelihood is **medium**: the precondition is a large amount, but the function is externally callable and the codebase pattern shows the check was intentionally applied elsewhere and simply omitted here.

---

### Recommendation

Add the same guard used in `depositCollateral` and `withdrawCollateral` at the top of `fastWithdrawalFeeAmount`:

```solidity
require(amount <= uint128(type(int128).max), ERR_CONVERSION_OVERFLOW);
```

This mirrors the existing pattern and prevents the silent wrap that corrupts `amountX18`.

---

### Proof of Concept

1. Call `fastWithdrawalFeeAmount(token_18_decimals, productId, 2**127)`.
2. `int128(2**127)` silently wraps to `type(int128).min` = `-170141183460469231731687303715884105728`.
3. `multiplier = 1` (18-decimal token), so `amountX18 = type(int128).min` (negative).
4. `proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18)` → negative value.
5. `MathHelper.max(negative, minFeeX18)` → returns `minFeeX18`.
6. The function returns the flat minimum fee for a withdrawal of `2^127` tokens — a near-total fee bypass. [4](#0-3)

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

**File:** core/contracts/Clearinghouse.sol (L199-205)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);
```

**File:** core/contracts/Clearinghouse.sol (L399-411)
```text
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
```
