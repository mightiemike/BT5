### Title
Unconditional Zero-Value ERC20 Transfer in Fast Withdrawal Fee Collection Blocks Fast Withdrawals - (File: `core/contracts/BaseWithdrawPool.sol`)

### Summary

`BaseWithdrawPool.submitFastWithdrawal` unconditionally calls `safeTransferFrom(token, msg.sender, uint128(fee))` in the third-party provider branch without checking whether `fee > 0`. When `withdrawFeeX18` is configured as zero for a product and the withdrawal amount is small enough that the proportional fee rounds to zero, the resulting zero-value transfer will revert for ERC20 tokens that disallow zero-value transfers, permanently blocking fast withdrawals for that product.

### Finding Description

In `BaseWithdrawPool.submitFastWithdrawal`, when `sendTo != msg.sender` (the third-party fast-withdrawal provider path), the fee is transferred unconditionally: [1](#0-0) 

The fee is computed by `fastWithdrawalFeeAmount`: [2](#0-1) 

The minimum fee component is `5 * spotEngine().getConfig(productId).withdrawFeeX18`. The `withdrawFeeX18` field in `ISpotEngine.Config` has no lower-bound validation: [3](#0-2) 

It is set directly from caller-supplied data in `SpotEngine.addOrUpdateProduct` with no non-zero check: [4](#0-3) 

When `withdrawFeeX18 = 0`, `minFeeX18 = 0`. The proportional component `FAST_WITHDRAWAL_FEE_RATE.mul(amountX18) / multiplier` also rounds to zero for small `transferAmount` values (e.g., for a 6-decimal token, any `transferAmount < 1000` native units yields `fee = 0`). With both components zero, `fee = 0` and the unconditional `safeTransferFrom(token, msg.sender, 0)` is issued.

`ERC20Helper.safeTransferFrom` uses a low-level call and requires `success == true`: [5](#0-4) 

Tokens that revert on zero-value transfers (a known ERC20 variant) will cause `success = false`, triggering `ERR_TRANSFER_FAILED` and reverting the entire `submitFastWithdrawal` call.

### Impact Explanation

Any fast withdrawal for a product configured with `withdrawFeeX18 = 0` where the computed fee rounds to zero will be permanently unprocessable via the third-party provider path (`sendTo != msg.sender`). The `markedIdxs[idx] = true` write occurs before the fee transfer, so a failed call marks the index as used without completing the withdrawal, permanently blocking that specific withdrawal index from being retried. [6](#0-5) 

### Likelihood Explanation

`withdrawFeeX18` can be legitimately set to zero by the owner (e.g., for a fee-free product). The proportional fee rounds to zero for any `transferAmount` below `1000` native units on a 6-decimal token (sub-cent amounts). The `sendTo != msg.sender` branch is the standard path for fast-withdrawal liquidity providers. The combination is realistic in a production deployment supporting low-value tokens or zero-fee products.

### Recommendation

Add a zero-check before the fee transfer, mirroring the fix pattern from the referenced report:

```solidity
} else {
    if (fee > 0) {
        safeTransferFrom(token, msg.sender, uint128(fee));
    }
}
```

Additionally, consider adding a `require(config.withdrawFeeX18 > 0)` guard in `SpotEngine.addOrUpdateProduct` to prevent zero-fee product configurations from being registered, or document that zero is a valid value and ensure all callers handle it.

### Proof of Concept

1. Owner calls `SpotEngine.addOrUpdateProduct` with `config.withdrawFeeX18 = 0` for product `productId = 2` backed by a token that reverts on zero-value transfers.
2. A user submits a `WithdrawCollateral` transaction for `amount = 500` (native units, 6-decimal token).
3. A fast-withdrawal provider calls `submitFastWithdrawal(idx, transaction, signatures)` with their own address as `msg.sender` and `sendTo` set to the user's address (`sendTo != msg.sender`).
4. `fastWithdrawalFeeAmount` computes: `proportionalFeeX18 = 1e15 * (500 * 1e12) / 1e18 = 500e9`; `feeX18 / multiplier = 500e9 / 1e12 = 0`.
5. `safeTransferFrom(token, msg.sender, 0)` is called; the token reverts; `ERR_TRANSFER_FAILED` is thrown.
6. `markedIdxs[idx]` was already set to `true` at step 3 line 88, so this withdrawal index is permanently consumed with no funds transferred. [7](#0-6)

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

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L23-31)
```text
    struct Config {
        address token;
        int128 interestInflectionUtilX18;
        int128 interestFloorX18;
        int128 interestSmallCapX18;
        int128 interestLargeCapX18;
        int128 withdrawFeeX18;
        int128 minDepositRateX18;
    }
```

**File:** core/contracts/SpotEngine.sol (L68-83)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;
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
