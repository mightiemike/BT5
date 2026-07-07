### Title
Fast Withdrawal Fee Charged to Provider Instead of User When `sendTo != msg.sender` — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

In `BaseWithdrawPool.submitFastWithdrawal`, when the withdrawal destination `sendTo` differs from `msg.sender` (the fast withdrawal provider), the protocol fee is pulled from `msg.sender` via `safeTransferFrom` while the full `transferAmount` is sent to `sendTo`. The fee should always be deducted from the user's withdrawal amount, not charged to the provider.

---

### Finding Description

`submitFastWithdrawal` resolves a signed withdrawal transaction and pays the user immediately from the pool's liquidity. The fee logic branches on whether `sendTo == msg.sender`:

```solidity
// BaseWithdrawPool.sol L104-L113
if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);          // fee deducted from user's amount ✓
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));  // fee pulled from provider ✗
}

fees[productId] += fee;
handleWithdrawTransfer(token, sendTo, transferAmount);  // full amount sent to user
```

When `sendTo != msg.sender` (the normal case for a third-party provider fronting funds for a user):

1. The pool sends the **full** `transferAmount` to `sendTo` (user pays no fee).
2. `msg.sender` (the provider) is charged the fee via `safeTransferFrom`.

The intended invariant — that the fee is borne by the withdrawing user, not the provider — is broken. The `WithdrawCollateralV2` path in `resolveFastWithdrawal` explicitly supports a custom `sendTo` address, making this the common case for fast withdrawals. [1](#0-0) 

The `resolveFastWithdrawal` function confirms that `sendTo` can be any arbitrary address specified in the signed transaction (not necessarily `msg.sender`): [2](#0-1) 

---

### Impact Explanation

The fast withdrawal provider (`msg.sender`) suffers a direct, quantifiable token loss equal to `fastWithdrawalFeeAmount(token, productId, transferAmount)` on every fast withdrawal where `sendTo != msg.sender`. The user (`sendTo`) receives the full signed withdrawal amount without paying any fee. The pool's `fees[productId]` accounting is correct in absolute terms, but the economic burden is misallocated: the provider subsidizes the user's fee. [3](#0-2) 

---

### Likelihood Explanation

`submitFastWithdrawal` is a public, permissionless function callable by any address. The `WithdrawCollateralV2` transaction type — which allows a user to specify an arbitrary `sendTo` — is explicitly supported in `resolveFastWithdrawal`. Any fast withdrawal where the provider is not also the recipient (i.e., the standard use case of a liquidity provider fronting funds for a user) triggers the bug. No special permissions or unusual conditions are required. [4](#0-3) 

---

### Recommendation

Remove the conditional branch. Always deduct the fee from `transferAmount`, regardless of whether `sendTo == msg.sender`:

```solidity
require(transferAmount > uint128(fee), "Fee larger than balance");
transferAmount -= uint128(fee);

fees[productId] += fee;
handleWithdrawTransfer(token, sendTo, transferAmount);
```

This ensures the fee is always borne by the withdrawing user (deducted from their received amount), not by the provider.

---

### Proof of Concept

1. User signs a `WithdrawCollateralV2` transaction: `amount = 10_000e6 USDC`, `sendTo = userAddress`.
2. Provider (`providerAddress != userAddress`) calls `submitFastWithdrawal(idx, transaction, signatures)`.
3. `resolveFastWithdrawal` returns `sendTo = userAddress`, `transferAmount = 10_000e6`.
4. `fee = fastWithdrawalFeeAmount(...)` — e.g., `50e6` USDC.
5. Since `sendTo (userAddress) != msg.sender (providerAddress)`, the `else` branch executes: `safeTransferFrom(token, providerAddress, 50e6)` — provider pays 50 USDC.
6. `handleWithdrawTransfer(token, userAddress, 10_000e6)` — user receives the full 10,000 USDC.
7. **Result**: Provider loses 50 USDC; user pays nothing. The fee should have been deducted from the user's 10,000 USDC, not taken from the provider. [5](#0-4)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L67-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
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
