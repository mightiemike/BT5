### Title
Incorrect Unit Scaling of `SLOW_MODE_FEE` Constant Causes Accounting Corruption — (`File: core/contracts/common/Constants.sol`)

---

### Summary

`SLOW_MODE_FEE` is defined as `1000000` with a comment of `// $1`, but the protocol's internal X18 fixed-point system represents $1 as `1e18`. Two other constants in the same file labeled `// $1` — `LIQUIDATION_FEE` and `HEALTHCHECK_FEE` — are both correctly set to `1e18`. `SLOW_MODE_FEE` is 12 orders of magnitude too small relative to the intended value, causing `slowModeFees` accounting to record near-zero fees while real USDC is collected, and making the `DepositInsurance` minimum-amount guard trivially bypassable.

---

### Finding Description

In `core/contracts/common/Constants.sol`, three constants are all labeled as representing `$1`:

```
int128 constant SLOW_MODE_FEE   = 1000000; // $1       ← 1e6
int128 constant LIQUIDATION_FEE = 1e18;    // $1       ← 1e18  ✓
int128 constant HEALTHCHECK_FEE = 1e18;    // $1       ← 1e18  ✓
```

The protocol uses `ONE = 10**18` as its X18 fixed-point base, so $1 = `1e18`. `SLOW_MODE_FEE = 1e6` is `$0.000000000001` in X18 terms — not $1.

`SLOW_MODE_FEE` is consumed in two distinct ways in `EndpointTx.sol`:

**1. Fee accounting accumulation (line 371):**
```solidity
chargeSlowModeFee(_getQuote(), sender);   // collects real USDC via ERC20 transfer
slowModeFees += SLOW_MODE_FEE;            // records 1e6 instead of 1e18
```
`chargeSlowModeFee` (in `EndpointStorage.sol`) calls `clearinghouse.getSlowModeFee()` and performs a real ERC20 `safeTransferFrom`, collecting actual USDC from the user. But `slowModeFees` is incremented by `1e6` — 12 orders of magnitude below the X18 value of $1. This desynchronizes the on-chain accounting ledger from actual collected funds.

**2. Minimum deposit guard for `DepositInsurance` (line 351):**
```solidity
require(txn.amount >= uint128(SLOW_MODE_FEE), ERR_DEPOSIT_TOO_SMALL);
```
If `txn.amount` is denominated in X18 (consistent with `MIN_DEPOSIT_AMOUNT = ONE / 10` and `MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE`), then `1e6` is an effectively zero minimum, allowing dust insurance deposits that bypass the intended $1 floor.

---

### Impact Explanation

- **`slowModeFees` accounting corruption**: The protocol accumulates `1e6` per slow-mode transaction instead of `1e18`. Any downstream logic in `Clearinghouse.sol` that reads `slowModeFees` and treats it as an X18 balance (consistent with all other fee accounting in the system) will see a value 10^12× smaller than the actual USDC collected. This corrupts the protocol's internal fee ledger relative to real collected funds.
- **Trivial minimum-deposit bypass**: Any unprivileged user submitting a `DepositInsurance` slow-mode transaction can pass the `ERR_DEPOSIT_TOO_SMALL` guard with a deposit of `1e6` X18 units (~$10^-12), defeating the intended $1 minimum.

---

### Likelihood Explanation

High. Every unprivileged user who submits any slow-mode transaction (withdraw collateral, link signer, etc.) triggers `slowModeFees += SLOW_MODE_FEE` at line 371. The accounting error accumulates with every such call. No special privileges or unusual conditions are required — this is the standard user-facing slow-mode submission path.

---

### Recommendation

Align `SLOW_MODE_FEE` with the X18 fixed-point convention used by all other dollar-denominated fee constants in the same file:

```solidity
// Before (incorrect):
int128 constant SLOW_MODE_FEE = 1000000; // $1

// After (correct):
int128 constant SLOW_MODE_FEE = 1e18; // $1
```

If the ERC20 transfer in `chargeSlowModeFee` must use raw USDC decimals (6), decouple the two usages: use a separate `SLOW_MODE_FEE_RAW = 1e6` for the ERC20 transfer and `SLOW_MODE_FEE = 1e18` for internal X18 accounting, rather than reusing the same constant for both.

---

### Proof of Concept

1. `ONE = 10**18` establishes the X18 base unit for $1. [1](#0-0) 
2. `LIQUIDATION_FEE = 1e18; // $1` and `HEALTHCHECK_FEE = 1e18; // $1` correctly encode $1 in X18. [2](#0-1) 
3. `SLOW_MODE_FEE = 1000000; // $1` is labeled $1 but is `1e6`, not `1e18` — 12 orders of magnitude too small. [3](#0-2) 
4. Every non-admin slow-mode submission executes `chargeSlowModeFee` (real USDC transfer) then `slowModeFees += SLOW_MODE_FEE` (records `1e6` instead of `1e18`), corrupting the fee ledger. [4](#0-3) 
5. The `DepositInsurance` minimum guard uses `SLOW_MODE_FEE` directly, making the $1 floor effectively zero in X18 terms. [5](#0-4) 
6. `chargeSlowModeFee` performs the real ERC20 transfer via `clearinghouse.getSlowModeFee()`, confirming actual USDC is collected while the accounting records a near-zero amount. [6](#0-5)

### Citations

**File:** core/contracts/common/Constants.sol (L17-17)
```text
int128 constant ONE = 10**18;
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L27-28)
```text
int128 constant LIQUIDATION_FEE = 1e18; // $1
int128 constant HEALTHCHECK_FEE = 1e18; // $1
```

**File:** core/contracts/EndpointTx.sol (L350-353)
```text
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
```

**File:** core/contracts/EndpointTx.sol (L370-371)
```text
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
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
