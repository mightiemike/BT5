### Title
Borrow Without Generating Normalized Debt Due to Rounding Toward Zero in `_updateBalanceNormalized` - (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

In `SpotEngineState._updateBalanceNormalized`, the normalized borrow balance (`amountNormalized`) is computed by dividing `newAmount` by `cumulativeBorrowsMultiplierX18` using `MathSD21x18.div`, which truncates toward zero. After any interest has accrued (`cumulativeBorrowsMultiplierX18 > 1e18`), a borrow of 1 wei of an 18-decimal token produces `newAmount = -1`, and the division `(-1 × 1e18) / cumulativeBorrowsMultiplierX18` truncates to `0`. No debt is recorded, yet the tokens are transferred out. An attacker can repeat this to drain the pool.

---

### Finding Description

Nado's spot engine tracks borrow positions using a normalized balance (`amountNormalized`), which is the analog of "debt shares" in the reference report. The conversion from a raw borrow delta to a normalized value occurs in `_updateBalanceNormalized`:

```solidity
// SpotEngineState.sol line 33-43
int128 newAmount = balance.amountNormalized.mul(
    cumulativeMultiplierX18
) + balanceDelta;

if (newAmount > 0) {
    cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
} else {
    cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
}

balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
```

`MathSD21x18.div` is defined as:

```solidity
// MathSD21x18.sol line 62-69
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
        int256 result = (int256(x) * ONE_X18) / y;  // ONE_X18 = 1e18
        ...
        return int128(result);
    }
}
```

So `newAmount.div(cumulativeBorrowsMultiplierX18)` computes `(newAmount × 1e18) / cumulativeBorrowsMultiplierX18` using Solidity integer division, which **truncates toward zero**.

For a 1-wei borrow of an 18-decimal token:
- `balanceDelta = -1` (from `amountRealized = -int128(1) * int128(1)` since multiplier = `10^(18-18) = 1`)
- `newAmount = -1`
- `result = (-1 × 1e18) / cumulativeBorrowsMultiplierX18`

`cumulativeBorrowsMultiplierX18` starts at `1e18` and grows with every interest tick. After any interest accrual, `cumulativeBorrowsMultiplierX18 > 1e18`, so `|result| < 1`, and truncation toward zero yields `0`.

The result: `balance.amountNormalized = 0` — no debt is recorded.

The call chain from `withdrawCollateral` in `Clearinghouse.sol` is:

```solidity
// Clearinghouse.sol lines 408-413
handleWithdrawTransfer(token, sendTo, amount, idx);   // tokens sent first
int128 amountRealized = -int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized); // debt recorded (rounds to 0)
spotEngine.assertUtilization(productId);              // passes: totalBorrows unchanged
require(getHealth(sender, healthType) >= 0, ...);     // passes: no debt on books
```

Because `amountNormalized = 0`, neither `totalBorrowsNormalized` nor the user's health is affected. All three post-conditions pass, and the attacker keeps the tokens.

---

### Impact Explanation

**Impact: High**

An attacker with a registered subaccount can repeatedly submit `WithdrawCollateral` transactions for 1 wei of any 18-decimal spot token (e.g., WETH). Each call transfers 1 wei to the attacker and records zero debt. Over many iterations the entire pool balance of that token can be drained. Depositors lose their principal. Other borrowers are unaffected in accounting terms, but the pool becomes insolvent: actual token reserves fall below what the accounting claims, so legitimate withdrawals will fail.

---

### Likelihood Explanation

**Likelihood: High**

The only preconditions are:
1. The target token has 18 decimals (standard for most ERC20s including WETH).
2. At least one `SpotTick` has been processed since the pool was initialized, causing `cumulativeBorrowsMultiplierX18 > 1e18`. This happens automatically in normal protocol operation.

No privileged access, oracle manipulation, or governance capture is required. The fast-path `WithdrawCollateral` and `WithdrawCollateralV2` transactions are user-signed and processed by the sequencer on-chain with no minimum-amount guard.

---

### Recommendation

When computing `amountNormalized` for a borrow (i.e., when `newAmount < 0`), round **away from zero** (toward negative infinity) rather than truncating toward zero. This ensures that any non-zero borrow always produces a non-zero normalized debt:

```diff
- balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
+ // For borrows (newAmount < 0), round toward -infinity to prevent zero-debt borrows
+ if (newAmount < 0) {
+     int256 raw = (int256(newAmount) * ONE_X18);
+     int256 divisor = int256(cumulativeMultiplierX18);
+     // floor division for negative numerator
+     balance.amountNormalized = int128(
+         (raw - (divisor - 1)) / divisor
+     );
+ } else {
+     balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
+ }
```

Alternatively, add a `require(balance.amountNormalized != 0 || newAmount == 0)` guard after the division to revert on zero-debt borrows.

---

### Proof of Concept

1. Alice deposits 100 WETH into the protocol (creates positive `totalDepositsNormalized`).
2. Time passes; the sequencer submits a `SpotTick`, causing `cumulativeBorrowsMultiplierX18` to grow above `1e18`.
3. Bob (attacker) has a registered subaccount with any collateral sufficient to pass the initial health check (e.g., a small USDC deposit).
4. Bob submits a signed `WithdrawCollateral` transaction: `productId = WETH_PRODUCT_ID`, `amount = 1`.
5. `Clearinghouse.withdrawCollateral` executes:
   - `handleWithdrawTransfer` sends 1 wei WETH to Bob.
   - `amountRealized = -1`.
   - `_updateBalanceNormalized` computes `newAmount = -1`, then `amountNormalized = (-1 × 1e18) / cumulativeBorrowsMultiplierX18 = 0`.
   - `assertUtilization` passes (accounting unchanged).
   - `getHealth` passes (Bob's balance shows 0 debt).
6. Bob repeats step 4 many times (e.g., 100 × 10^18 iterations for 100 WETH), draining the pool.
7. Alice attempts to withdraw her 100 WETH and the transaction reverts because the clearinghouse has no WETH balance.

**Key lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/SpotEngineState.sol (L33-43)
```text
        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
```

**File:** core/contracts/libraries/MathSD21x18.sol (L62-69)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L408-413)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
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
