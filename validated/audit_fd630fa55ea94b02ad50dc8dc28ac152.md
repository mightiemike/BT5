### Title
`int128` Overflow in Decimal-Scaling Multiplication Causes `submitFastWithdrawal` to Revert for Large Amounts — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.fastWithdrawalFeeAmount()` multiplies two `int128` values — a user-supplied amount and a decimal-scaling multiplier — without widening to `int256` first. Because the guard `require(transferAmount <= INT128_MAX)` only checks the amount in isolation, the product `int128(amount) * int128(multiplier)` can silently overflow `int128` in Solidity 0.8.x (which reverts), causing a legitimate fast withdrawal to be permanently blocked. The identical pattern is repeated in `Clearinghouse.depositCollateral`, `depositInsurance`, `withdrawInsurance`, and `checkMinDeposit`.

---

### Finding Description

`BaseWithdrawPool.submitFastWithdrawal` is a `public` function. After verifying signatures it calls `fastWithdrawalFeeAmount`:

```solidity
// BaseWithdrawPool.sol line 100
require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

// BaseWithdrawPool.sol line 102
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);
```

Inside `fastWithdrawalFeeAmount`:

```solidity
// line 141
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
// line 142
int128 amountX18 = int128(amount) * int128(multiplier);   // ← overflow
``` [1](#0-0) 

`MAX_DECIMALS = 18` and `INT128_MAX = type(int128).max ≈ 1.7 × 10^38`. [2](#0-1) 

The guard at line 100 only ensures `amount ≤ INT128_MAX`. It does **not** ensure `amount × multiplier ≤ INT128_MAX`. For any token whose `decimals < 18`, `multiplier > 1`, so the product can exceed `int128` even when both operands individually fit. Solidity 0.8.x arithmetic is checked by default, so the overflow reverts.

**Concrete overflow threshold by token decimals:**

| Token decimals | multiplier | Overflow when `amount >` |
|---|---|---|
| 0 | 10^18 | ~1.7 × 10^20 (native units) |
| 6 (USDC) | 10^12 | ~1.7 × 10^26 (native units) |
| 8 (WBTC) | 10^10 | ~1.7 × 10^28 (native units) |

The same unchecked `int128(amount) * int128(multiplier)` pattern appears in:

- `Clearinghouse.depositCollateral` line 205 [3](#0-2) 
- `Clearinghouse.depositInsurance` line 265 [4](#0-3) 
- `Clearinghouse.withdrawInsurance` line 282 [5](#0-4) 
- `Clearinghouse.checkMinDeposit` line 708 [6](#0-5) 

---

### Impact Explanation

Any caller who submits a valid, properly signed fast-withdrawal transaction whose `amount` exceeds `INT128_MAX / multiplier` will have the call revert inside `fastWithdrawalFeeAmount`. The withdrawal is permanently blocked on the fast path (the `markedIdxs[idx]` flag is set to `true` at line 88 **before** the overflow occurs, so the same `idx` cannot be retried). [7](#0-6) 

The user's funds remain locked in the pool and the fast-withdrawal slot is consumed. The same overflow in `depositCollateral` would cause the sequencer's deposit processing to revert, permanently blocking the deposit for that submission index.

---

### Likelihood Explanation

The overflow threshold for a 0-decimal token is ~1.7 × 10^20 native units, and for a 6-decimal token ~1.7 × 10^26 native units. These are large but not impossible values for high-supply tokens or tokens with non-standard decimal configurations. The guard `require(amount <= INT128_MAX)` gives users a false sense of safety — it passes for amounts that still cause overflow after scaling. Any integrator or user who constructs a withdrawal at the boundary of `INT128_MAX` (which the guard explicitly permits) will trigger the revert.

---

### Recommendation

Perform the scaling multiplication in `int256` space and then range-check before narrowing to `int128`, mirroring the pattern already used correctly in `MathSD21x18`:

```solidity
// BaseWithdrawPool.sol fastWithdrawalFeeAmount
int256 amountX18_256 = int256(int128(amount)) * multiplier;
require(
    amountX18_256 >= type(int128).min && amountX18_256 <= type(int128).max,
    ERR_CONVERSION_OVERFLOW
);
int128 amountX18 = int128(amountX18_256);
```

Apply the same fix to every site in `Clearinghouse.sol` that performs `int128(amount) * int128(multiplier)`.

---

### Proof of Concept

1. Deploy a token with `decimals = 0`. `multiplier = 10^18`.
2. A user obtains a valid signed `WithdrawCollateral` transaction with `amount = 2 × 10^20` (passes `<= INT128_MAX` since INT128_MAX ≈ 1.7 × 10^38).
3. User calls `submitFastWithdrawal`. `markedIdxs[idx]` is set to `true` at line 88.
4. Execution reaches line 142: `int128(2×10^20) * int128(10^18)` = `2×10^38 > INT128_MAX` → Solidity 0.8.x reverts with overflow.
5. The withdrawal slot `idx` is now permanently marked; the user cannot retry the same fast withdrawal. [8](#0-7)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L86-113)
```text
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
```

**File:** core/contracts/BaseWithdrawPool.sol (L139-143)
```text
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

```

**File:** core/contracts/common/Constants.sol (L19-30)
```text
uint8 constant MAX_DECIMALS = 18;

int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00

int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%

int128 constant LIQUIDATION_FEE = 1e18; // $1
int128 constant HEALTHCHECK_FEE = 1e18; // $1

uint128 constant INT128_MAX = uint128(type(int128).max);
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

**File:** core/contracts/Clearinghouse.sol (L261-266)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        insurance += amount;
```

**File:** core/contracts/Clearinghouse.sol (L278-283)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        require(amount <= insurance, ERR_NO_INSURANCE);
```

**File:** core/contracts/Clearinghouse.sol (L703-708)
```text
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        uint8 decimals = _decimals(productId);
        require(decimals <= MAX_DECIMALS);

        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(multiplier) * int128(amount);
```
