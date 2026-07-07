### Title
Unsigned Integer Underflow in `getSlowModeFee()` Permanently Bricks All Slow Mode Transactions for Sub-6-Decimal Quote Tokens — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.getSlowModeFee()` computes a fee multiplier using `token.decimals() - 6` where `token.decimals()` is a `uint8`. If the quote token has fewer than 6 decimals, this subtraction underflows and reverts in Solidity 0.8+. Because `getSlowModeFee()` is called in the critical path of every non-privileged slow mode transaction submission, the entire slow mode queue is permanently bricked for any such quote token.

---

### Finding Description

`getSlowModeFee()` in `Clearinghouse.sol` computes the fee amount to charge users submitting slow mode transactions:

```solidity
function getSlowModeFee() external view returns (uint256) {
    ISpotEngine spotEngine = _spotEngine();
    IERC20Base token = IERC20Base(
        spotEngine.getConfig(QUOTE_PRODUCT_ID).token
    );
    int256 multiplier = int256(10**(token.decimals() - 6));
    return uint256(int256(SLOW_MODE_FEE) * multiplier);
}
``` [1](#0-0) 

`token.decimals()` returns a `uint8`. The expression `token.decimals() - 6` is evaluated as unsigned 8-bit arithmetic. In Solidity ≥0.8, if `token.decimals() < 6`, this underflows and the transaction reverts with a panic.

The constant `SLOW_MODE_FEE = 1000000` is defined as `$1` in 6-decimal units: [2](#0-1) 

The formula `10**(token.decimals() - 6)` is intended to scale from the 6-decimal base to the actual token's decimal precision. It silently assumes `decimals >= 6` with no guard.

`getSlowModeFee()` is consumed by `chargeSlowModeFee()` in `EndpointStorage.sol`:

```solidity
function chargeSlowModeFee(IERC20Base token, address from) internal virtual {
    require(address(token) != address(0));
    token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
}
``` [3](#0-2) 

`chargeSlowModeFee` is called in `submitSlowModeTransactionImpl()` for every non-privileged slow mode transaction type:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [4](#0-3) 

This covers withdrawals, transfers, and all other user-initiated slow mode operations. There is no `require(decimals >= 6)` guard anywhere in the quote token registration or deposit path — only an upper-bound guard `require(decimals <= MAX_DECIMALS)` exists in `depositCollateral`: [5](#0-4) 

---

### Impact Explanation

**Impact: High.** If the quote token has fewer than 6 decimals, every call to `submitSlowModeTransaction()` by any unprivileged user reverts at the fee-charging step. This permanently bricks the slow mode queue — users cannot submit withdrawals, transfers, or any other slow mode transaction. Funds deposited into the protocol become unrecoverable through the slow mode path. The corrupted state is the entire slow mode transaction queue: `slowModeTxs` can never be appended to by users, and `slowModeFees` is never incremented.

---

### Likelihood Explanation

**Likelihood: Low.** The bug is triggered only when the quote token has fewer than 6 decimals. Most stablecoins used as quote tokens (USDC, USDT) have exactly 6 decimals, making `token.decimals() - 6 = 0` safe. However, no on-chain guard prevents a sub-6-decimal token from being configured as the quote token, and the protocol is designed to be deployable across chains where such tokens may exist.

---

### Recommendation

Cast to a signed type before subtracting, mirroring the fix in the referenced report:

```diff
- int256 multiplier = int256(10**(token.decimals() - 6));
+ int8 decimalsDiff = int8(token.decimals()) - 6;
+ uint256 multiplier = decimalsDiff >= 0
+     ? 10**uint8(decimalsDiff)
+     : 1; // or handle downscaling: fee / 10**uint8(-decimalsDiff)
+ return uint256(int256(SLOW_MODE_FEE) * int256(multiplier));
```

Alternatively, add an explicit guard at quote token registration time: `require(token.decimals() >= 6)`.

---

### Proof of Concept

1. Deploy the protocol with a quote token whose `decimals()` returns `2` (e.g., a hypothetical 2-decimal stablecoin).
2. Any user calls `Endpoint.submitSlowModeTransaction(withdrawalTx)`.
3. Execution reaches `submitSlowModeTransactionImpl()` → `chargeSlowModeFee(_getQuote(), sender)` → `clearinghouse.getSlowModeFee()`.
4. Inside `getSlowModeFee()`: `token.decimals()` returns `2`; `2 - 6` underflows as `uint8` arithmetic → Solidity 0.8 panic revert.
5. The transaction reverts. No slow mode transaction can ever be submitted by any user. All deposited funds are locked with no slow mode withdrawal path.

### Citations

**File:** core/contracts/Clearinghouse.sol (L201-204)
```text
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
```

**File:** core/contracts/Clearinghouse.sol (L759-766)
```text
    function getSlowModeFee() external view returns (uint256) {
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(
            spotEngine.getConfig(QUOTE_PRODUCT_ID).token
        );
        int256 multiplier = int256(10**(token.decimals() - 6));
        return uint256(int256(SLOW_MODE_FEE) * multiplier);
    }
```

**File:** core/contracts/common/Constants.sol (L19-23)
```text
uint8 constant MAX_DECIMALS = 18;

int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00

int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```
