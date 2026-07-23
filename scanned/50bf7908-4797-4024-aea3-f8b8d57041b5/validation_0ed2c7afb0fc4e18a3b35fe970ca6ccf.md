### Title
`DepositAllowlistExtension` gates on position `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and checks the `owner` parameter instead. Because `MetricOmmPool.addLiquidity` lets any `msg.sender` supply an arbitrary `owner`, an unprivileged caller can bypass the allowlist entirely by naming any allowlisted address as the position owner.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool calls:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument (`msg.sender`) is the **actual depositor**; the second (`owner`) is the address that will own the resulting position. These are two distinct roles — any caller can specify any `owner`.

`DepositAllowlistExtension.beforeAddLiquidity` receives these as `(address /*sender*/, address owner, ...)` but **ignores the first argument entirely** and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

An unauthorized caller (Bob) calls `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension checks `allowedDepositor[pool][Alice]` = `true` and passes. Bob provides the tokens via the swap callback, and Alice receives the position. The allowlist is completely bypassed.

This is directly inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swap caller):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The `BaseMetricExtension` default for `beforeAddLiquidity` enforces `onlyPool` but provides no allowlist logic — the allowlist logic is entirely in `DepositAllowlistExtension`, and it checks the wrong address. [4](#0-3) 

---

### Impact Explanation

Any unprivileged address can add liquidity to a pool protected by `DepositAllowlistExtension` by naming any allowlisted address as `owner`. The pool admin's intent to restrict deposits to specific addresses (e.g., KYC'd LPs, whitelisted counterparties) is completely violated. This is a broken core pool access-control path: the allowlist extension is the only guard on `addLiquidity`, and it is bypassable by any caller with knowledge of one allowlisted address. [5](#0-4) 

---

### Likelihood Explanation

The bypass requires only:
1. Knowledge of one allowlisted address — observable on-chain from past `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.
2. The ability to call `pool.addLiquidity(owner = allowlisted_address, ...)` and provide tokens via callback.

No special privileges, flash loans, or complex setup are required. Any EOA or contract can execute this in a single transaction. [6](#0-5) 

---

### Recommendation

Change `beforeAddLiquidity` to check the `sender` parameter (the actual depositor) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

```solidity
// Setup: pool admin allows only Alice to deposit
depositAllowlist.setAllowedToDeposit(pool, alice, true);
// alice is allowlisted; bob is NOT

// Bob bypasses the allowlist by naming Alice as owner
vm.startPrank(bob);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// Extension checks allowedDepositor[pool][alice] = true → passes
// Bob provides tokens via metricOmmAddLiquidityCallback
// Alice receives the position; Bob's tokens enter the pool
pool.addLiquidity(
    alice,   // owner = alice (allowlisted) — bypasses the check
    0,       // salt
    deltas,  // liquidity delta
    "",      // callbackData
    ""       // extensionData
);
vm.stopPrank();

// Bob successfully deposited into a pool he is not authorized to touch.
// The DepositAllowlistExtension did not revert.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-14)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L45-52)
```text
  function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
