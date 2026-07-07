### Title
Missing Decimal Lower-Bound Validation in `getSlowModeFee` Causes Arithmetic Underflow, Permanently Blocking All Slow-Mode Transactions — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.getSlowModeFee()` computes `token.decimals() - 6` without first validating that the quote token has at least 6 decimals. In Solidity 0.8+, a `uint8` subtraction that underflows reverts unconditionally. Because `EndpointTx.submitSlowModeTransactionImpl` calls `getSlowModeFee()` to determine the fee to charge, any deployment where the quote token carries fewer than 6 decimals permanently bricks the entire slow-mode path — including user-initiated withdrawals.

---

### Finding Description

`getSlowModeFee` in `Clearinghouse.sol` converts the protocol-constant `SLOW_MODE_FEE` from its internal representation into native token units:

```solidity
function getSlowModeFee() external view returns (uint256) {
    ISpotEngine spotEngine = _spotEngine();
    IERC20Base token = IERC20Base(
        spotEngine.getConfig(QUOTE_PRODUCT_ID).token
    );
    int256 multiplier = int256(10**(token.decimals() - 6));   // ← no guard
    return uint256(int256(SLOW_MODE_FEE) * multiplier);
}
``` [1](#0-0) 

`token.decimals()` returns a `uint8`. The expression `token.decimals() - 6` is evaluated as `uint8` arithmetic. Under Solidity 0.8's checked arithmetic, if `token.decimals() < 6` the subtraction reverts with an arithmetic underflow panic.

Every other decimal-normalization site in the same contract guards against the analogous condition before performing the subtraction:

```solidity
// depositCollateral — guarded
require(decimals <= MAX_DECIMALS);
int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
``` [2](#0-1) 

```solidity
// fastWithdrawalFeeAmount — guarded
require(decimals <= MAX_DECIMALS);
int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
``` [3](#0-2) 

`getSlowModeFee` is the only decimal-subtraction site that carries no such guard, and it subtracts a fixed constant (`6`) rather than `MAX_DECIMALS`, making the lower-bound assumption implicit and unvalidated.

`EndpointTx` calls `getSlowModeFee` inside `submitSlowModeTransactionImpl` to determine the fee amount to deduct from the submitter. Because `submitSlowModeTransaction` in `Endpoint` delegates entirely to `EndpointTx.submitSlowModeTransactionImpl`, a revert inside `getSlowModeFee` propagates all the way up and causes every call to `submitSlowModeTransaction` to revert. [4](#0-3) 

The `SLOW_MODE_FEE` constant and `getSlowModeFee` are declared together, confirming the fee is always expressed relative to 6 decimal places: [5](#0-4) 

---

### Impact Explanation

If the quote token (`QUOTE_PRODUCT_ID`) is configured with fewer than 6 decimals, `getSlowModeFee()` reverts on every call. This makes `submitSlowModeTransaction` permanently uncallable by any user. Slow-mode transactions include user-initiated collateral withdrawals (`WithdrawCollateral`), insurance operations, and other critical settlement actions. Users who have deposited funds cannot withdraw them through the slow-mode path, resulting in effective fund lock-up for the duration of the misconfiguration.

---

### Likelihood Explanation

Tokens with fewer than 6 decimals exist in production (e.g., tokens with 2 or 4 decimals). The protocol does not enforce a minimum decimal count when a quote token is registered via `addEngine` / `addOrUpdateProduct`. A deployer who selects such a token as the quote asset — or a future migration to a lower-decimal stablecoin — silently introduces this condition. The trigger requires no attacker action: the broken state is entered at configuration time and affects every subsequent user interaction with the slow-mode path.

---

### Recommendation

Add a lower-bound decimal check in `getSlowModeFee` before performing the subtraction, mirroring the pattern already used in `depositCollateral` and `fastWithdrawalFeeAmount`:

```solidity
function getSlowModeFee() external view returns (uint256) {
    ISpotEngine spotEngine = _spotEngine();
    IERC20Base token = IERC20Base(
        spotEngine.getConfig(QUOTE_PRODUCT_ID).token
    );
    uint8 decimals = token.decimals();
    require(decimals >= 6, "quote token decimals too low");   // ← add this
    int256 multiplier = int256(10**(decimals - 6));
    return uint256(int256(SLOW_MODE_FEE) * multiplier);
}
```

Additionally, enforce `decimals >= 6` (or the appropriate minimum) when registering a quote token so the invariant is guaranteed at the source rather than at every use site.

---

### Proof of Concept

1. Deploy the protocol with a quote token whose `decimals()` returns `2` (e.g., a 2-decimal stablecoin).
2. Any user calls `endpoint.submitSlowModeTransaction(withdrawalTx)`.
3. `Endpoint.submitSlowModeTransaction` delegates to `EndpointTx.submitSlowModeTransactionImpl`.
4. `submitSlowModeTransactionImpl` calls `clearinghouse.getSlowModeFee()`.
5. Inside `getSlowModeFee`, `token.decimals()` returns `2`; the expression `2 - 6` underflows `uint8` under Solidity 0.8 checked arithmetic and reverts.
6. The revert propagates through the delegatecall back to the user — the withdrawal transaction is never queued.
7. Every subsequent call to `submitSlowModeTransaction` by any user reverts identically; deposited funds cannot be withdrawn via the slow-mode path. [1](#0-0)

### Citations

**File:** core/contracts/Clearinghouse.sol (L203-204)
```text
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

**File:** core/contracts/BaseWithdrawPool.sol (L140-141)
```text
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```
