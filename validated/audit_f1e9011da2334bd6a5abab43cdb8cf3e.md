### Title
Zero-Value `safeTransferFrom` in `submitFastWithdrawal` Blocks Fast Withdrawals for Tokens That Revert on Zero Transfers — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal()` unconditionally calls `safeTransferFrom(token, msg.sender, uint128(fee))` when `sendTo != msg.sender`, even when the computed `fee` rounds down to zero via integer division in `fastWithdrawalFeeAmount()`. For ERC20 tokens that revert on zero-value transfers, this permanently blocks the fast withdrawal path for any withdrawal where the fee truncates to zero.

---

### Finding Description

In `fastWithdrawalFeeAmount()`, the fee is computed as:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
int128 amountX18 = int128(amount) * int128(multiplier);
int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;
int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);   // <-- integer division truncates
``` [1](#0-0) 

For a 6-decimal token (e.g., USDC), `multiplier = 10^12`. If `feeX18 < 10^12`, the division truncates to `0`. This happens when:
- `proportionalFeeX18` is small (small withdrawal amount), **and**
- `minFeeX18 = 5 * withdrawFeeX18 = 0` (i.e., `withdrawFeeX18` is configured as zero for the product).

The returned `fee = 0` is then used unconditionally in the `sendTo != msg.sender` branch:

```solidity
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));  // transfers 0
}
``` [2](#0-1) 

`safeTransferFrom` delegates to `ERC20Helper.safeTransferFrom`, which calls `token.transferFrom(from, to, 0)` with no zero-amount guard: [3](#0-2) 

For tokens that revert on zero-value transfers, this causes `submitFastWithdrawal` to revert entirely.

---

### Impact Explanation

The fast withdrawal path (`submitFastWithdrawal`) is permanently broken for any collateral token that:
1. Reverts on zero-value transfers, **and**
2. Has a `withdrawFeeX18 = 0` configuration, **and**
3. Is used with a withdrawal amount small enough that `feeX18 < multiplier`.

The `idx` is marked as used (`markedIdxs[idx] = true`) **before** the fee transfer is attempted: [4](#0-3) 

This means the revert unwinds the `markedIdxs` write (since it's in the same transaction), but the user's funds remain locked in the protocol until the sequencer processes the slow-mode withdrawal — defeating the purpose of fast withdrawal entirely.

---

### Likelihood Explanation

- `submitFastWithdrawal` is a `public` function callable by any liquidity provider without any privileged access.
- Tokens with low decimals (6–8) and small withdrawal amounts are common in production (USDC, USDT).
- A `withdrawFeeX18 = 0` configuration is plausible for newly listed or zero-fee products.
- The attacker-controlled entry is simply submitting a valid fast withdrawal transaction for a small amount.

---

### Recommendation

Add a zero-amount guard before the `safeTransferFrom` call, mirroring the fix recommended in M-03:

```solidity
if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);
} else {
    if (uint128(fee) > 0) {
        safeTransferFrom(token, msg.sender, uint128(fee));
    }
}
``` [5](#0-4) 

---

### Proof of Concept

1. Deploy a 6-decimal ERC20 token that reverts on zero-value transfers.
2. Register it as a collateral product with `withdrawFeeX18 = 0`.
3. A user signs a `WithdrawCollateral` transaction for `amount = 1` (1 raw unit = 0.000001 token).
4. A liquidity provider calls `submitFastWithdrawal` with `sendTo != msg.sender`.
5. `fastWithdrawalFeeAmount` computes:
   - `multiplier = 10^12`
   - `amountX18 = 1 * 10^12 = 10^12`
   - `proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE * 10^12 / 10^18` → rounds to 0 for any `FAST_WITHDRAWAL_FEE_RATE < 10^6`
   - `minFeeX18 = 0` (since `withdrawFeeX18 = 0`)
   - `feeX18 = 0`, `fee = 0 / 10^12 = 0`
6. `safeTransferFrom(token, msg.sender, 0)` is called → token reverts → entire `submitFastWithdrawal` reverts. [6](#0-5)

### Citations

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

**File:** core/contracts/BaseWithdrawPool.sol (L139-148)
```text
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
