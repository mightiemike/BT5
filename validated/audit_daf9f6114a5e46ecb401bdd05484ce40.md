### Title
Unconditional Zero-Value `safeTransferFrom` in `submitFastWithdrawal` Blocks Fast Withdrawals for Tokens That Revert on Zero Transfers — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

In `BaseWithdrawPool.submitFastWithdrawal`, when `sendTo != msg.sender`, the protocol collects a fee via `safeTransferFrom(token, msg.sender, uint128(fee))` without first checking whether `fee > 0`. When the computed fee is zero — a reachable outcome given the fee formula — and the collateral token reverts on zero-value transfers, every fast withdrawal attempt for that product will revert, permanently blocking the fast withdrawal path.

---

### Finding Description

`submitFastWithdrawal` resolves a signed withdrawal transaction, computes a fee, and branches on whether the caller is the recipient:

```solidity
// BaseWithdrawPool.sol lines 102–113
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);
} else {
    safeTransferFrom(token, msg.sender, uint128(fee)); // unconditional — no zero-check
}

fees[productId] += fee;
handleWithdrawTransfer(token, sendTo, transferAmount);
``` [1](#0-0) 

The fee is computed by `fastWithdrawalFeeAmount`:

```solidity
// BaseWithdrawPool.sol lines 134–149
int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;
int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
return feeX18 / int128(multiplier);
``` [2](#0-1) 

Two independent paths produce `fee = 0`:

1. **`withdrawFeeX18 = 0` for the product**: `minFeeX18 = 5 * 0 = 0`. This is a valid configuration — the field is set per-product by `addOrUpdateProduct` with no lower-bound enforcement. [3](#0-2) 

2. **Small withdrawal amount**: `proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18)` uses `MathSD21x18` fixed-point multiplication (`a * b / 1e18`). With `FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000` (0.1%), any `amountX18 < 1000` rounds to zero. [4](#0-3) 

When both conditions hold, `fee = 0` and the `else` branch unconditionally calls:

```solidity
safeTransferFrom(token, msg.sender, uint128(0));
```

`safeTransferFrom` in `ERC20Helper` uses a low-level `call` and requires `success == true`:

```solidity
// ERC20Helper.sol lines 29–41
(bool success, bytes memory data) = address(self).call(
    abi.encodeWithSelector(IERC20Base.transferFrom.selector, from, to, amount)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [5](#0-4) 

For tokens that revert on zero-value transfers (a known class of non-standard ERC20 tokens, e.g., LEND, BNB), `success = false`, and the entire `submitFastWithdrawal` call reverts.

---

### Impact Explanation

Any fast withdrawal provider attempting to service a `WithdrawCollateralV2` transaction where `sendTo != msg.sender` — the standard case when a user specifies a custom recipient — will have their call revert whenever the fee rounds to zero. The fast withdrawal path is completely blocked for that product/token combination. Users are forced onto the slow withdrawal path, which enforces a `SLOW_MODE_TX_DELAY = 3 days` delay. [6](#0-5) 

---

### Likelihood Explanation

The `WithdrawCollateralV2` path explicitly supports a custom `sendTo` address, making `sendTo != msg.sender` a normal, intended use case: [7](#0-6) 

A product configured with `withdrawFeeX18 = 0` combined with a small withdrawal amount (< 1000 wei for 18-decimal tokens) is realistic, particularly for low-value or dust withdrawals. The combination of a non-standard ERC20 token and this configuration is the triggering condition, placing likelihood at medium.

---

### Recommendation

Add a zero-guard before the fee transfer in the `else` branch of `submitFastWithdrawal`:

```solidity
} else {
    if (fee > 0) {
        safeTransferFrom(token, msg.sender, uint128(fee));
    }
}
```

This mirrors the fix recommended in the external report and eliminates the unconditional zero-value transfer.

---

### Proof of Concept

1. A spot product is added via `addOrUpdateProduct` with `config.withdrawFeeX18 = 0`.
2. A user signs a `WithdrawCollateralV2` transaction specifying `sendTo = someOtherAddress` (not the fast withdrawal provider's address) and `amount = 500` (for an 18-decimal token, this is 500 wei).
3. A fast withdrawal provider calls `submitFastWithdrawal(idx, transaction, signatures)`.
4. `fastWithdrawalFeeAmount` computes: `proportionalFeeX18 = 0.001 * 500 / 1e18 = 0` (integer truncation); `minFeeX18 = 5 * 0 = 0`; `fee = 0`.
5. Since `sendTo != msg.sender`, the `else` branch executes: `safeTransferFrom(token, msg.sender, 0)`.
6. The token (e.g., LEND) reverts on zero-value `transferFrom`.
7. `ERC20Helper.safeTransferFrom` catches `success = false` and reverts with `ERR_TRANSFER_FAILED`.
8. The fast withdrawal is permanently blocked; the user must wait 3 days for the slow path. [8](#0-7)

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

**File:** core/contracts/BaseWithdrawPool.sol (L144-148)
```text
        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
```

**File:** core/contracts/SpotEngine.sol (L68-97)
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

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```

**File:** core/contracts/common/Constants.sol (L25-25)
```text
int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/libraries/ERC20Helper.sol (L29-41)
```text
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
```
