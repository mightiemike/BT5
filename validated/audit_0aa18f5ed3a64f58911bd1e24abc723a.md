Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Position `owner` Instead of Actual `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller) and checks the LP position `owner` instead. Because `MetricOmmPool.addLiquidity` allows any caller to deposit on behalf of any owner, an address not in the allowlist can bypass the guard entirely by depositing on behalf of an allowlisted owner.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values to the extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and silently discarded. The guard checks `owner` instead: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [4](#0-3) 

The `isAllowedToDeposit` view function takes a `depositor` parameter, confirming the intent is to gate the depositor, not the owner: [5](#0-4) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly supports depositing on behalf of a different owner, with `msg.sender` as the payer: [6](#0-5) 

The wrong value is `allowedDepositor[msg.sender][owner]` — it should be `allowedDepositor[msg.sender][sender]`. The extension decision (`beforeAddLiquidity.selector` vs. revert) is determined by the wrong address, defeating the allowlist entirely.

## Impact Explanation

An address not in the allowlist can deposit into a restricted pool by calling `addLiquidityExactShares(pool, allowlisted_owner, ...)`. The extension evaluates `allowedDepositor[pool][allowlisted_owner]` → `true` → guard passes. The unauthorized depositor pays the tokens; the position is credited to the allowlisted owner. The pool admin's intent to restrict who can deposit is completely defeated. This is a direct admin-boundary break: an unprivileged actor bypasses a pool-admin-configured security boundary with direct liquidity-flow consequences. The inverse also holds: an allowlisted `sender` depositing on behalf of a non-allowlisted `owner` is incorrectly blocked, breaking legitimate operator use.

## Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` router explicitly and publicly supports the `owner != msg.sender` deposit pattern. Any actor who knows an allowlisted address (observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` mapping reads) can exploit this with no special privileges. The only cost is paying the deposited tokens, which the attacker controls. The exploit is repeatable and requires no setup beyond identifying an allowlisted owner address.

## Recommendation

Name the `sender` parameter and check it instead of `owner`, matching the pattern used by `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured for `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, bob, true)` — only Bob is allowlisted.
3. Alice (not allowlisted) calls `addLiquidityExactShares(pool, bob, salt, deltas, max0, max1, "")` via `MetricOmmPoolLiquidityAdder`.
4. The pool calls `_beforeAddLiquidity(router_address, bob, ...)`, which encodes and calls `beforeAddLiquidity(router_address, bob, ...)` on the extension.
5. Extension evaluates `allowedDepositor[pool][bob]` → `true` → guard passes.
6. Alice's tokens are pulled via callback; Bob's LP position is credited. Alice has successfully deposited into a pool she is not authorized to access.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
