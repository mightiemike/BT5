### Title
Fast Withdrawal Fee Truncates to Zero for Small Amounts on Low-Decimal Tokens — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`fastWithdrawalFeeAmount` in `BaseWithdrawPool.sol` converts a fee computed in 18-decimal fixed-point back to native token decimals using a plain truncating integer division. For any token with fewer than 18 decimals (e.g., USDC at 6), the divisor (`multiplier`) is large enough that a user-controlled withdrawal amount below a calculable threshold produces a fee of exactly zero, allowing the fast-withdrawal service to be consumed for free.

---

### Finding Description

`fastWithdrawalFeeAmount` works in three steps:

1. Scale `amount` up to 18-decimal precision: `amountX18 = amount * multiplier` where `multiplier = 10^(18 - decimals)`.
2. Compute the fee in X18 space: `feeX18 = max(proportionalFeeX18, minFeeX18)`.
3. Scale back to native decimals: `return feeX18 / int128(multiplier)`.

Step 3 is a plain Solidity integer division — it truncates toward zero. Whenever `feeX18 < multiplier`, the returned fee is `0`. [1](#0-0) 

For USDC (6 decimals), `multiplier = 10^12`. The proportional fee path gives:

```
proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18)
                   = (1e15 * amount * 1e12) / 1e18
                   = amount * 1e9
``` [2](#0-1) 

Dividing back: `fee = (amount * 1e9) / 1e12 = amount / 1000`. This is zero for any `amount < 1000` raw USDC units (i.e., `< 0.001 USDC`).

The `minFeeX18` floor is equally affected: `minFeeX18 / multiplier` also truncates to zero whenever `5 * withdrawFeeX18 < 10^12`. [3](#0-2) 

`submitFastWithdrawal` then proceeds with `fee = 0`:

- **`sendTo == msg.sender` path**: `transferAmount -= 0`, so the user receives the full amount.
- **`sendTo != msg.sender` path**: `safeTransferFrom(token, msg.sender, 0)` transfers nothing. [4](#0-3) 

---

### Impact Explanation

The fast-withdrawal service provides immediate token liquidity ahead of the normal sequencer queue. The 0.1% fee (`FAST_WITHDRAWAL_FEE_RATE`) is the protocol's compensation for that service. A user who withdraws amounts below the truncation threshold (e.g., `< 0.001 USDC` per transaction) pays zero fee while still receiving immediate settlement. Accumulated across many transactions, this drains the fast-withdrawal liquidity pool without any fee revenue accruing to `fees[productId]`. [5](#0-4) 

---

### Likelihood Explanation

The entry point `submitFastWithdrawal` is `public` with no access control beyond valid sequencer signatures on the embedded transaction. A user controls the `amount` field in their signed `WithdrawCollateral` or `WithdrawCollateralV2` transaction. Ink Chain (the target network) is an L2 with low gas costs, making repeated small-amount fast withdrawals economically viable. The threshold (0.001 USDC for a 6-decimal token) is easily reachable by any user. [6](#0-5) 

---

### Recommendation

Round the fee up instead of truncating it down. Replace the final return statement:

```solidity
// current — truncates toward zero
return feeX18 / int128(multiplier);
```

with a ceiling division for positive fees:

```solidity
// fixed — rounds up
int128 multiplierI = int128(multiplier);
return (feeX18 + multiplierI - 1) / multiplierI;
```

This ensures that any non-zero `feeX18` always produces at least 1 unit of fee in native token decimals, consistent with the recommendation in the referenced report. [7](#0-6) 

---

### Proof of Concept

**Setup:** USDC token, 6 decimals. `FAST_WITHDRAWAL_FEE_RATE = 1e15` (0.1%). `multiplier = 10^12`.

**Step 1 — User signs a `WithdrawCollateral` transaction with `amount = 999` (raw USDC units = 0.000999 USDC).**

**Step 2 — Anyone calls `submitFastWithdrawal` with valid sequencer signatures.**

**Step 3 — Fee computation:**
```
amountX18        = 999 * 1e12 = 999e12
proportionalFeeX18 = (1e15 * 999e12) / 1e18 = 999e9
minFeeX18        = 5 * withdrawFeeX18  (assume small, e.g. 1e11 → 5e11)
feeX18           = max(999e9, 5e11) = 5e11
fee              = 5e11 / 1e12 = 0  ← truncated to zero
```

**Step 4 — `sendTo == msg.sender`:** `transferAmount -= 0`. User receives 999 raw USDC with zero fee paid.

**Step 5 — `fees[productId] += 0`.** No revenue recorded. [8](#0-7) [9](#0-8)

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

**File:** core/contracts/common/Constants.sol (L25-25)
```text
int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```

**File:** core/contracts/libraries/MathSD21x18.sol (L54-59)
```text
    function mul(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            int256 result = (int256(x) * y) / ONE_X18;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```
