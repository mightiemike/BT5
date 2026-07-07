### Title
Zero-Value `safeTransferFrom` in `submitFastWithdrawal` Blocks Fast Withdrawals for Tokens That Revert on Zero Transfers - (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` unconditionally calls `safeTransferFrom` to collect the fast-withdrawal fee from a third-party submitter even when the computed fee is zero. Tokens that revert on zero-value transfers will cause the entire fast-withdrawal transaction to revert, permanently blocking that withdrawal path for affected products.

---

### Finding Description

In `submitFastWithdrawal`, when `sendTo != msg.sender` (a third party submits the withdrawal on behalf of the recipient), the fee is collected via:

```solidity
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));
}
``` [1](#0-0) 

There is no guard checking `fee > 0` before this call. The fee is computed by `fastWithdrawalFeeAmount`:

```solidity
int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;
int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);
``` [2](#0-1) 

`FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000` (0.1%), and `MathSD21x18.mul` performs truncating integer division `(x * y) / 1e18`: [3](#0-2) 

So `proportionalFeeX18 = amountX18 / 1000`. For any `amountX18 < 1000` (i.e., a withdrawal amount below 1000 in X18 units), this truncates to zero. If `withdrawFeeX18` is also configured as zero for the product, then `minFeeX18 = 0`, `feeX18 = 0`, and `fee = 0`. [4](#0-3) 

`withdrawFeeX18` is a per-product configurable field in `ISpotEngine.Config`: [5](#0-4) 

The quote product initializes `withdrawFeeX18 = ONE`, but other products can be configured with `withdrawFeeX18 = 0`. When that is the case and the withdrawal amount is small, `fee` resolves to zero and `safeTransferFrom(token, msg.sender, 0)` is called unconditionally.

`ERC20Helper.safeTransferFrom` performs a raw low-level call with no zero-amount guard: [6](#0-5) 

---

### Impact Explanation

Any token that reverts on zero-value transfers (e.g., LEND, and others documented at [weird-erc20](https://github.com/d-xo/weird-erc20#revert-on-zero-value-transfers)) will cause `submitFastWithdrawal` to revert entirely when the fee rounds to zero. The fast-withdrawal path for that product is completely blocked for third-party submitters. The signed withdrawal transaction cannot be executed via the fast path, forcing the user to wait for the slow-mode path (3-day delay), which is a meaningful denial of the fast-withdrawal service.

---

### Likelihood Explanation

The condition requires: (1) a product configured with `withdrawFeeX18 = 0`, (2) a withdrawal amount small enough that `proportionalFeeX18` truncates to zero, and (3) the product's base token reverts on zero-value transfers. All three conditions are independently plausible in production. The function is `public` with no access control beyond signature verification, so any caller can trigger this path. [7](#0-6) 

---

### Recommendation

Add a zero-check before the fee transfer in the `else` branch of `submitFastWithdrawal`:

```solidity
} else {
    if (uint128(fee) > 0) {
        safeTransferFrom(token, msg.sender, uint128(fee));
    }
}
```

This mirrors the recommended fix from the external report and is consistent with how `DirectDepositV1.creditDeposit` already guards its transfer with `if (balance != 0)`. [8](#0-7) 

---

### Proof of Concept

1. Deploy a product with `withdrawFeeX18 = 0` backed by a token that reverts on zero-value transfers.
2. A user signs a `WithdrawCollateral` transaction for a small amount (e.g., 1 unit in native decimals, which maps to `amountX18 < 1000`).
3. A third-party relayer calls `submitFastWithdrawal(idx, transaction, signatures)` with `msg.sender != sendTo`.
4. `fastWithdrawalFeeAmount` returns `fee = 0` (both `proportionalFeeX18` and `minFeeX18` are zero).
5. `safeTransferFrom(token, msg.sender, 0)` is called; the token reverts.
6. The fast withdrawal is permanently blocked for this `idx` — `markedIdxs[idx]` was already set to `true` at line 88 before the revert, so re-submission is also blocked. [9](#0-8)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-85)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
```

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

**File:** core/contracts/BaseWithdrawPool.sol (L144-148)
```text
        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
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

**File:** core/contracts/common/Constants.sol (L25-25)
```text
int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
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

**File:** core/contracts/DirectDepositV1.sol (L91-99)
```text
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
```
