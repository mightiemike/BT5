### Title
Small Borrow Normalization Rounding Allows Debt-Free Token Withdrawal — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`_updateBalanceNormalized` in `SpotEngineState.sol` normalizes a borrow delta by dividing by `cumulativeBorrowsMultiplierX18` using truncating integer division. For the minimum representable borrow amount (1 wei of an 18-decimal token), once any interest has accrued and the multiplier exceeds `1e18`, the normalized debt rounds to exactly zero. The actual token transfer in `withdrawCollateral` uses the raw `amount` parameter and executes unconditionally before the balance update, so the user receives tokens while recording zero debt. The health check passes because the recorded balance remains zero.

---

### Finding Description

In `_updateBalanceNormalized`, the normalized borrow amount is computed as:

```solidity
balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
``` [1](#0-0) 

The `div` function in `MathSD21x18` performs:

```solidity
int256 result = (int256(x) * ONE_X18) / y;
``` [2](#0-1) 

This is truncating integer division (toward zero). For a borrow of `newAmount = -1` (1 wei of an 18-decimal token, where `multiplier = 10^(18-18) = 1`):

```
result = (-1 * 1e18) / cumulativeBorrowsMultiplierX18
```

Once `cumulativeBorrowsMultiplierX18 > 1e18` (i.e., after any interest has accrued), `|result| < 1`, which truncates to `0`. The normalized debt is stored as zero, and `totalBorrowsNormalized` is not updated:

```solidity
} else {
    state.totalBorrowsNormalized -= balance.amountNormalized; // -= 0, no-op
}
``` [3](#0-2) 

In `withdrawCollateral`, the actual ERC-20 transfer executes **before** the balance update:

```solidity
handleWithdrawTransfer(token, sendTo, amount, idx);   // token leaves the contract
...
spotEngine.updateBalance(productId, sender, amountRealized); // debt rounds to 0
spotEngine.assertUtilization(productId);              // passes: totalBorrows unchanged
...
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH); // passes: balance = 0
``` [4](#0-3) 

Because `amountNormalized` rounds to zero, `_setBalanceAndUpdateBitmap` clears the product bit (`hasBalance = false`), so the product is excluded from health calculations entirely: [5](#0-4) 

The health check sees a zero balance, returns health = 0 ≥ 0, and the withdrawal succeeds.

---

### Impact Explanation

Each iteration extracts 1 wei of an 18-decimal spot token from the Clearinghouse's reserves without recording any debt. The protocol's `totalBorrowsNormalized` accounting diverges from the actual token balance held by the contract. The `assertUtilization` invariant (`totalDeposits >= totalBorrows`) is maintained in accounting terms but the actual on-chain token balance silently decreases. Over many iterations, the protocol's real reserves are drained while its internal accounting shows no liability.

---

### Likelihood Explanation

Conditions required:
1. The spot product must use an 18-decimal ERC-20 token (so `multiplier = 1` and `amountRealized = -1`).
2. Any interest must have accrued (`cumulativeBorrowsMultiplierX18 > 1e18`), which happens naturally after the first `SpotTick` on a product with non-zero utilization.
3. The attacker needs only a registered subaccount — no collateral is required.

18-decimal tokens (e.g., WETH, WBTC-equivalents) are standard. Interest accrues automatically. The attack is permissionless and repeatable. However, the extracted value per transaction is 1 wei, making the attack economically irrational at current gas prices — the gas cost per transaction far exceeds the extracted token value. Practical exploitation is therefore limited to scenarios where gas costs approach zero or the token has extreme per-wei value.

---

### Recommendation

In `_updateBalanceNormalized`, when `newAmount` is negative (a borrow), round the normalized amount away from zero (i.e., toward more negative) rather than truncating toward zero. This ensures the recorded debt is never less than the actual borrow:

```solidity
// For borrows (newAmount < 0), round down (more negative) instead of truncating toward 0
int128 normalized = newAmount.div(cumulativeMultiplierX18);
if (newAmount < 0 && normalized.mul(cumulativeMultiplierX18) != newAmount) {
    normalized -= 1; // round away from zero for borrows
}
balance.amountNormalized = normalized;
```

Alternatively, enforce a minimum borrow size in `withdrawCollateral` such that `amountRealized` is always large enough that `div(amountRealized, cumulativeBorrowsMultiplierX18) != 0`.

---

### Proof of Concept

1. Deploy with a spot product using an 18-decimal token (e.g., WETH).
2. Wait for any `SpotTick` to be processed with non-zero utilization, causing `cumulativeBorrowsMultiplierX18` to exceed `1e18`.
3. Register a subaccount with zero collateral.
4. Submit a `WithdrawCollateral` transaction with `amount = 1` (1 wei).
5. Observe: `handleWithdrawTransfer` sends 1 wei to the attacker; `_updateBalanceNormalized` computes `div(-1, cumulativeBorrowsMultiplierX18) = 0`; `totalBorrowsNormalized` is unchanged; health check passes with health = 0.
6. Repeat step 4 indefinitely. Each iteration extracts 1 wei with zero recorded debt.

```
Before: Clearinghouse token balance = B, totalBorrowsNormalized = T
After N iterations: Clearinghouse token balance = B - N, totalBorrowsNormalized = T (unchanged)
Attacker balance: +N wei, recorded debt: 0
```

### Citations

**File:** core/contracts/SpotEngineState.sol (L43-43)
```text
        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
```

**File:** core/contracts/SpotEngineState.sol (L45-49)
```text
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
```

**File:** core/contracts/SpotEngineState.sol (L199-208)
```text
    function _setBalanceAndUpdateBitmap(
        uint32 productId,
        bytes32 subaccount,
        BalanceNormalized memory balance
    ) internal {
        balances[productId][subaccount] = balance;
        bool hasBalance = balance.amountNormalized != 0;
        _setProductBit(subaccount, productId, hasBalance);
        _balanceUpdate(productId, subaccount);
    }
```

**File:** core/contracts/libraries/MathSD21x18.sol (L62-68)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```

**File:** core/contracts/Clearinghouse.sol (L408-419)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
