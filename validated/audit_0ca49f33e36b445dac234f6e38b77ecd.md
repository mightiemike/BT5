### Title
Zero-Value Fee Transfer in `submitFastWithdrawal` Blocks Fast Withdrawals for Revert-on-Zero ERC20 Tokens - (File: `core/contracts/BaseWithdrawPool.sol`)

### Summary

In `BaseWithdrawPool.submitFastWithdrawal`, when `sendTo != msg.sender`, the computed fee from `fastWithdrawalFeeAmount` is passed unconditionally to `safeTransferFrom`. Due to integer division truncation in `fastWithdrawalFeeAmount`, the fee can evaluate to exactly `0` for small withdrawal amounts on low-decimal tokens. ERC20 tokens that revert on zero-value transfers (e.g., LEND) will cause `submitFastWithdrawal` to revert entirely, permanently blocking the fast withdrawal path for those token/amount combinations.

---

### Finding Description

`fastWithdrawalFeeAmount` computes the fee as:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));  // e.g. 10^12 for 6-decimal token
int128 amountX18 = int128(amount) * int128(multiplier);
int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18); // 0.1% of amountX18
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;
int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);  // integer division — can truncate to 0
``` [1](#0-0) 

With `FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000` (0.1%) and `MAX_DECIMALS = 18`: [2](#0-1) 

For a **6-decimal token** (e.g., USDC) and `amount = 1` (i.e., 0.000001 USDC):
- `multiplier = 10^12`
- `amountX18 = 1 * 10^12 = 10^12`
- `proportionalFeeX18 = MathSD21x18.mul(10^15, 10^12) = 10^15 * 10^12 / 10^18 = 10^9`
- If `withdrawFeeX18 = 0`, then `minFeeX18 = 0`, so `feeX18 = 10^9`
- `return 10^9 / 10^12 = 0` ← integer division truncates to zero

The returned `fee = 0` is then used unconditionally in the `else` branch of `submitFastWithdrawal`:

```solidity
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));  // fee == 0 → zero-value transfer
}
``` [3](#0-2) 

`safeTransferFrom` calls `token.safeTransferFrom(from, address(this), amount)` with `amount = 0`: [4](#0-3) 

For tokens that revert on zero-value transfers, this call reverts, and the entire `submitFastWithdrawal` transaction fails.

---

### Impact Explanation

Any user attempting a fast withdrawal (`sendTo != msg.sender`) of a small amount of a zero-value-transfer-reverting ERC20 token will have their transaction permanently reverted. The fast withdrawal path is completely blocked for such token/amount combinations. The `markedIdxs[idx] = true` write at line 88 occurs **before** the fee transfer, meaning the `idx` is consumed and the withdrawal cannot be retried via the same index. [5](#0-4) 

---

### Likelihood Explanation

- The `sendTo != msg.sender` branch is a supported, documented fast-withdrawal path where a third party pays the fee on behalf of the withdrawer.
- Low-decimal tokens (6 or 8 decimals) are common (USDC, USDT, WBTC). Small withdrawal amounts are realistic for dust or micro-transaction scenarios.
- `withdrawFeeX18` can be zero — it is a configurable field in `ISpotEngine.Config` with no enforced lower bound.
- The protocol does not restrict which ERC20 tokens can be listed as spot products. [6](#0-5) 

---

### Recommendation

Add a zero-check before calling `safeTransferFrom` in the `else` branch:

```solidity
} else {
    if (fee > 0) {
        safeTransferFrom(token, msg.sender, uint128(fee));
    }
}
```

Similarly, apply the same guard to `handleWithdrawTransfer` if `transferAmount` can be zero.

---

### Proof of Concept

Arithmetic demonstration for a 6-decimal token with `withdrawFeeX18 = 0` and `amount = 1`:

```
MAX_DECIMALS = 18
decimals = 6
multiplier = 10^(18-6) = 10^12

amountX18 = 1 * 10^12 = 1_000_000_000_000

FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000  // 0.1% in X18
proportionalFeeX18 = (1_000_000_000_000_000 * 1_000_000_000_000) / 10^18
                   = 10^27 / 10^18
                   = 10^9 = 1_000_000_000

minFeeX18 = 5 * 0 = 0

feeX18 = max(1_000_000_000, 0) = 1_000_000_000

fee = feeX18 / multiplier = 1_000_000_000 / 10^12 = 0  ← truncated to zero

// In submitFastWithdrawal, sendTo != msg.sender branch:
safeTransferFrom(token, msg.sender, uint128(0))  // ← reverts for LEND-like tokens
``` [7](#0-6) [2](#0-1)

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

**File:** core/contracts/BaseWithdrawPool.sol (L192-198)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```

**File:** core/contracts/common/Constants.sol (L19-25)
```text
uint8 constant MAX_DECIMALS = 18;

int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00

int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```
