### Title
Hardcoded Decimal Base of `6` in `getSlowModeFee()` Causes Accounting Desynchronization for Non-6-Decimal Quote Tokens — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.getSlowModeFee()` hardcodes `6` as the assumed base decimal of the quote token when scaling `SLOW_MODE_FEE` to native token units. When the quote token has decimals other than 6, the fee amount charged to users via `chargeSlowModeFee` diverges from the amount recorded in the `slowModeFees` accumulator, corrupting the protocol's internal fee accounting state. Additionally, if the quote token has fewer than 6 decimals, the subtraction `token.decimals() - 6` underflows in Solidity 0.8+, causing `getSlowModeFee()` to revert and blocking all user-initiated slow mode transactions.

---

### Finding Description

`SLOW_MODE_FEE` is defined as `1000000` — representing $1 denominated in 6-decimal units (i.e., USDC-native units). [1](#0-0) 

`getSlowModeFee()` scales this constant to the quote token's native decimals using a hardcoded base of `6`:

```solidity
int256 multiplier = int256(10**(token.decimals() - 6));
return uint256(int256(SLOW_MODE_FEE) * multiplier);
``` [2](#0-1) 

`chargeSlowModeFee` calls `clearinghouse.getSlowModeFee()` to determine how many native tokens to pull from the user: [3](#0-2) 

Immediately after charging the user, `submitSlowModeTransactionImpl` increments the `slowModeFees` accumulator by the raw constant `SLOW_MODE_FEE = 1000000`, regardless of the actual token's decimals:

```solidity
chargeSlowModeFee(_getQuote(), sender);
slowModeFees += SLOW_MODE_FEE;
``` [4](#0-3) 

**Desynchronization**: When the quote token has 18 decimals, `getSlowModeFee()` returns `1000000 * 10^12 = 10^18` (correct $1 in 18-decimal units), but `slowModeFees` is incremented by only `1000000` — a factor of `10^12` smaller than the actual fee collected. The `slowModeFees` state variable permanently diverges from the true accumulated fee balance.

**Underflow revert**: If the quote token has fewer than 6 decimals (e.g., 2 decimals), `uint8(2) - 6` underflows in Solidity 0.8+, causing `getSlowModeFee()` to revert. Since `chargeSlowModeFee` calls `getSlowModeFee()`, every user-initiated slow mode transaction that reaches the fee-charging branch reverts unconditionally. [5](#0-4) 

---

### Impact Explanation

**Accounting corruption**: `slowModeFees` is a persistent on-chain state variable that tracks accumulated slow mode fees. When the quote token has decimals ≠ 6, `slowModeFees` records a value that is off by a factor of `10^(decimals - 6)` relative to the actual token amounts collected. Any downstream logic — including off-chain sequencer assertions, future on-chain reads, or protocol invariant checks — that relies on `slowModeFees` reflecting the true collected fee amount will operate on a corrupted value.

**Blocked slow mode path**: For quote tokens with fewer than 6 decimals, `getSlowModeFee()` reverts unconditionally, making it impossible for any unprivileged user to submit a slow mode transaction (e.g., `WithdrawCollateral`, `LinkSigner`). This is a complete loss of the slow mode fallback path for affected users.

---

### Likelihood Explanation

The protocol's `depositCollateral` and `fastWithdrawalFeeAmount` functions correctly use `MAX_DECIMALS - token.decimals()` to handle arbitrary token decimals, demonstrating that multi-decimal support is an explicit design goal. The `getSlowModeFee()` function is the only place where `6` is hardcoded as the decimal base rather than derived from `MAX_DECIMALS`. Any deployment using a quote token with decimals ≠ 6 (e.g., an 18-decimal stablecoin or a 2-decimal token) triggers the bug. Likelihood is **Medium** given the protocol's stated support for multiple collateral tokens.

---

### Recommendation

Replace the hardcoded `6` with a dynamic read of the quote token's actual decimals, consistent with how the rest of the protocol handles decimal normalization:

```solidity
function getSlowModeFee() external view returns (uint256) {
    ISpotEngine spotEngine = _spotEngine();
    IERC20Base token = IERC20Base(
        spotEngine.getConfig(QUOTE_PRODUCT_ID).token
    );
    uint8 decimals = token.decimals();
    require(decimals <= MAX_DECIMALS, "decimals overflow");
    int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
    // SLOW_MODE_FEE is expressed in X18; divide back to native units
    return uint256(int256(SLOW_MODE_FEE) / multiplier);
}
```

Also align `slowModeFees += SLOW_MODE_FEE` to accumulate in the same X18 unit as `SLOW_MODE_FEE` is defined, or convert it to native token units consistently.

---

### Proof of Concept

1. Deploy the protocol with a quote token that has 18 decimals (e.g., a WETH-denominated deployment or an 18-decimal stablecoin).
2. Any user calls `submitSlowModeTransaction` with a transaction type that is not owner-only (e.g., `WithdrawCollateral`).
3. `submitSlowModeTransactionImpl` reaches line 370: `chargeSlowModeFee(_getQuote(), sender)`.
4. `chargeSlowModeFee` calls `clearinghouse.getSlowModeFee()`.
5. `getSlowModeFee()` computes `multiplier = 10**(18 - 6) = 10^12` and returns `1000000 * 10^12 = 10^18`. The user is charged `10^18` native tokens ($1 correct).
6. Control returns to line 371: `slowModeFees += SLOW_MODE_FEE` → `slowModeFees += 1000000`.
7. After N such transactions, `slowModeFees = N * 1000000`, but the actual tokens collected = `N * 10^18`. The accumulator is `10^12` times smaller than reality — a permanent, compounding accounting corruption.

For the underflow case: repeat with a 2-decimal quote token. Step 5 computes `10**(2 - 6)` → Solidity 0.8+ arithmetic underflow → revert. All slow mode transactions for unprivileged users are permanently blocked. [2](#0-1) [6](#0-5) [3](#0-2)

### Citations

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
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

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
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
