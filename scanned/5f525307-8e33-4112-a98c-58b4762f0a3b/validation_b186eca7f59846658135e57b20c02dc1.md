### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is intended to gate `addLiquidity` by the depositor's address. Its `beforeAddLiquidity` hook silently checks the `owner` parameter (the position beneficiary) rather than the `sender` parameter (the actual caller who pays tokens). Any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner`, depositing real tokens into a restricted pool while the guard never inspects the true caller.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments in order — `sender` first, `owner` second:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and discarded; the guard reads only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Because `owner` is a free caller-supplied parameter in `addLiquidity`, any address can pass an allowlisted address as `owner` and the guard passes unconditionally. The callback is then invoked on `msg.sender` (the actual, unauthorized caller), who pays the tokens:

```solidity
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
``` [4](#0-3) 

The position is then credited to the allowlisted `owner`, not the actual caller.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [5](#0-4) 

The inconsistency confirms the deposit extension has the wrong parameter bound.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict who may inject liquidity into a restricted pool. With this bug the guard is completely ineffective: any unprivileged address can deposit real tokens into the pool by naming any allowlisted address as `owner`. The pool receives tokens from unauthorized sources, the allowlisted address receives an unsolicited position, and the pool admin's access-control boundary is silently broken. This is a direct admin-boundary break — an unprivileged path bypasses a pool-admin-configured guard — matching the contest's allowed impact category.

---

### Likelihood Explanation

The exploit requires no special privilege, no flash loan, and no complex setup. Any EOA or contract that can call `addLiquidity` and implement the `IMetricOmmModifyLiquidityCallback` interface can trigger it in a single transaction. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping.

---

### Recommendation

Bind the guard to `sender` (the actual caller), mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob deploys a contract implementing `IMetricOmmModifyLiquidityCallback` that transfers the required tokens to the pool.
3. Bob calls `pool.addLiquidity(owner = alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
4. `_beforeAddLiquidity(msg.sender=Bob, owner=alice, ...)` is forwarded to the extension.
5. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` credits the position to `alice` and calls Bob's callback; Bob's contract transfers tokens to the pool.
7. Alice now holds a position she never requested; Bob's tokens are permanently locked in the pool under Alice's key. The allowlist has been fully bypassed. [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-39)
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
```
