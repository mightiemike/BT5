### Title
Inverted Fee Condition in `submitFastWithdrawal` Charges Provider Instead of Deducting from Withdrawal Amount — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` contains an inverted conditional that governs fast-withdrawal fee handling. In the normal third-party fast-withdrawal path (`sendTo != msg.sender`), the fee is charged to the calling provider via `safeTransferFrom` rather than being deducted from the withdrawal amount. The user receives the full `transferAmount` without paying the fast-withdrawal fee, while the provider bears the fee cost out of pocket.

---

### Finding Description

`submitFastWithdrawal` resolves `sendTo` from the signed withdrawal transaction (always the user's address for `WithdrawCollateral`, or a custom address for `WithdrawCollateralV2`). The fee branch is:

```solidity
if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));
}

fees[productId] += fee;
handleWithdrawTransfer(token, sendTo, transferAmount);
``` [1](#0-0) 

The `if` branch (fee deducted from withdrawal) only fires when `sendTo == msg.sender` — i.e., when the caller is the recipient, which is the self-service case. The `else` branch (fee charged to provider) fires in every third-party fast-withdrawal call, which is the dominant use case.

The correct invariant is: **the fast-withdrawal fee must always be deducted from `transferAmount`**, regardless of who calls the function. The condition is inverted relative to this invariant.

Additionally, the `require(transferAmount > uint128(fee))` guard exists only in the `if` branch. In the `else` branch there is no such check, so a provider could be charged a fee exceeding the withdrawal amount while the user still receives the full `transferAmount`. [2](#0-1) 

The `safeTransferFrom` helper transfers tokens from the provider to the pool contract:

```solidity
function safeTransferFrom(IERC20Base token, address from, uint256 amount) internal virtual {
    token.safeTransferFrom(from, address(this), amount);
}
``` [3](#0-2) 

This requires the provider to have pre-approved the pool contract as a spender. Providers who have not done so will revert on every third-party fast-withdrawal call, effectively blocking the feature entirely.

---

### Impact Explanation

- **Fee bypass**: Users whose withdrawals are processed by a third-party provider receive the full `transferAmount` with no fee deducted. The fast-withdrawal fee is entirely bypassed from the user's perspective.
- **Provider financial loss**: The provider pays the fee out of pocket via `safeTransferFrom`. For large withdrawals, `fastWithdrawalFeeAmount` returns `max(FAST_WITHDRAWAL_FEE_RATE × amount, 5 × withdrawFeeX18)`, which can be substantial.
- **Feature breakage**: Any provider without a pre-existing ERC-20 approval to the pool will have every third-party fast-withdrawal call revert, making the fast-withdrawal path non-functional for the standard use case.
- **No `require` guard in `else` branch**: If `fee > transferAmount`, the provider is charged more than the withdrawal amount while the user still receives `transferAmount` in full. [4](#0-3) 

---

### Likelihood Explanation

The `sendTo != msg.sender` path is the **normal** fast-withdrawal scenario: `resolveFastWithdrawal` for `WithdrawCollateral` always sets `sendTo` to the user's address derived from the signed transaction, which will differ from the provider's address in every standard call. Any provider attempting to process a fast withdrawal for a user triggers the inverted branch unconditionally. [5](#0-4) 

---

### Recommendation

Remove the `safeTransferFrom` from the `else` branch. Always deduct the fee from `transferAmount` and apply the `require` guard unconditionally:

```solidity
require(transferAmount > uint128(fee), "Fee larger than balance");
transferAmount -= uint128(fee);
fees[productId] += fee;
handleWithdrawTransfer(token, sendTo, transferAmount);
```

---

### Proof of Concept

1. User subm

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L56-66)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            return (
                signedTx.tx.productId,
                address(uint160(bytes20(signedTx.tx.sender))),
                signedTx.tx.amount
            );
        }
```

**File:** core/contracts/BaseWithdrawPool.sol (L100-113)
```text
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
