### Title
`int128` Multiplication Overflow Reverts Deposits and Fast Withdrawals for Large Amounts — (`core/contracts/Clearinghouse.sol`, `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

The same arithmetic overflow class from the external report exists in three Nado production functions. A `multiplier` value is computed as `int256` but immediately cast to `int128` before being multiplied against a user-supplied `int128` amount. Because the multiplication is performed entirely in `int128` arithmetic, sufficiently large deposit or withdrawal amounts cause an overflow revert in Solidity 0.8.x, permanently blocking those operations for affected token amounts.

---

### Finding Description

`MAX_DECIMALS = 18` is defined in `Constants.sol`. For any token with fewer than 18 decimals, the normalization multiplier is `10^(18 - decimals)`. This value is computed as `int256` but then truncated to `int128` before the multiplication:

**`Clearinghouse.sol::depositCollateral` (line 204–205):**
```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
int128 amountRealized = int128(txn.amount) * int128(multiplier);
``` [1](#0-0) 

**`BaseWithdrawPool.sol::fastWithdrawalFeeAmount` (line 141–142):**
```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
int128 amountX18 = int128(amount) * int128(multiplier);
``` [2](#0-1) 

**`Clearinghouse.sol::checkMinDeposit` (line 707–708):**
```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
int128 amountRealized = int128(multiplier) * int128(amount);
``` [3](#0-2) 

In all three cases the existing guard only checks that `amount <= INT128_MAX` (≈ 1.7e38), which ensures the raw amount fits in `int128`. It does **not** guard against the product `amount * multiplier` overflowing `int128`. [4](#0-3) [5](#0-4) 

`int128` max = `2^127 − 1 ≈ 1.7e38`. The overflow threshold for the product is:

| Token decimals | `multiplier` | Overflow when raw `amount` > |
|---|---|---|
| 0 | 10^18 | ~1.7e20 tokens |
| 6 (USDC) | 10^12 | ~1.7e26 raw units (~1.7e20 USDC) |
| 8 | 10^10 | ~1.7e28 raw units |

Because Solidity 0.8.x performs checked arithmetic by default and none of these lines are inside an `unchecked` block, the overflow causes a hard revert. [6](#0-5) 

---

### Impact Explanation

- **`depositCollateral`**: A user deposit transaction processed by the endpoint reverts. The user's collateral is not credited; the deposit is permanently blocked for that amount.
- **`fastWithdrawalFeeAmount` / `submitFastWithdrawal`**: `submitFastWithdrawal` is `public` and calls `fastWithdrawalFeeAmount` directly. Any fast withdrawal with a sufficiently large amount reverts, locking the user out of the fast-withdrawal path.
- **`checkMinDeposit`**: Reverts instead of returning `false`, which can break any caller that relies on it to gate deposits. [7](#0-6) 

---

### Likelihood Explanation

The threshold is high (~1.7e20 human-readable tokens for 0-decimal tokens, ~1.7e20 USDC for 6-decimal tokens), so routine retail-sized deposits are unaffected. However, the protocol is designed to support institutional-scale collateral and high-supply tokens. Any whale deposit or programmatic integration that approaches these thresholds will trigger an irrecoverable revert with no informative error message, making diagnosis difficult. The `fastWithdrawalFeeAmount` path is directly callable by any user without sequencer involvement, making it the most immediately reachable trigger.

---

### Recommendation

Widen the multiplication to `int256` before performing it, then cast the result back to `int128` after the division, mirroring the fix recommended in the external report:

```solidity
// depositCollateral and checkMinDeposit
int128 amountRealized = int128(
    (int256(txn.amount) * multiplier) // multiplier already int256
);

// fastWithdrawalFeeAmount
int128 amountX18 = int128(int256(amount) * multiplier);
```

Since `multiplier` is already declared as `int256`, no additional cast is needed on that side — only `amount` needs to be widened before the multiplication.

---

### Proof of Concept

For `submitFastWithdrawal` with a 6-decimal token (e.g., USDC):

```
amount = 1_700_000_000_000_000_000_000_000_000  // 1.7e27 raw USDC units
multiplier = 10^12

int128(amount) * int128(multiplier)
= 1.7e27 * 1e12
= 1.7e39  >  INT128_MAX (≈ 1.7e38)
→ Solidity 0.8.x overflow revert
```

A user submitting a valid signed fast-withdrawal transaction for this amount will receive a revert from `fastWithdrawalFeeAmount` at line 142, blocking the withdrawal entirely. [8](#0-7)

### Citations

**File:** core/contracts/Clearinghouse.sol (L199-199)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
```

**File:** core/contracts/Clearinghouse.sol (L204-205)
```text
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);
```

**File:** core/contracts/Clearinghouse.sol (L707-708)
```text
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(multiplier) * int128(amount);
```

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

**File:** core/contracts/BaseWithdrawPool.sol (L141-142)
```text
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
