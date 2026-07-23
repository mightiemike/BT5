Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing non-allowlisted callers to bypass the deposit guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller) and only validates `owner` (the position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` permits `owner != msg.sender` with no restriction, any non-allowlisted address can bypass the guard by supplying an allowlisted address as `owner`, depositing tokens into that address's position without authorization.

## Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that may differ from `msg.sender` and forwards both to extensions:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes and dispatches both `sender` and `owner` to every configured extension:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and therefore silently ignored. Only `owner` is evaluated:

```solidity
// DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This is structurally inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` and ignores the recipient. The asymmetry is uniquely exploitable on the deposit path because `removeLiquidity` enforces `msg.sender == owner` (L206), so the owner/sender split only exists for `addLiquidity`.

After the guard passes, `LiquidityLib.addLiquidity` mints position shares keyed to `owner` (Alice) and invokes the callback on `msg.sender` (Bob), who provides the tokens. Bob's tokens are deposited into Alice's position with no allowlist check on Bob.

## Impact Explanation
The pool admin's deposit allowlist is completely bypassed. Any address can deposit into a pool restricted to specific depositors by supplying an allowlisted address as `owner`. Pools using this extension for KYC/AML compliance, institutional access control, or permissioned liquidity programs are rendered unprotected. This is a confirmed admin-boundary break: an unprivileged path circumvents a pool admin–configured access-control guard.

## Likelihood Explanation
The attacker needs only to know one allowlisted address, which is readable from the public `allowedDepositor` mapping. No special privileges are required. The attacker loses the deposited tokens (they go into the allowlisted address's position), so the motivation is regulatory evasion or griefing rather than direct profit. The guard is fully defeated regardless.

## Recommendation
Check `sender` (the actual caller) instead of `owner`, consistent with `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the caller and the position owner, check both addresses.

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` configured as a `beforeAddLiquidity` hook.
2. Pool admin allowlists Alice: `setAllowedToDeposit(pool, Alice, true)`.
3. Bob (not allowlisted) deploys a contract implementing `metricOmmModifyLiquidityCallback` that transfers tokens from Bob to the pool.
4. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
5. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
6. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
7. `LiquidityLib.addLiquidity` mints shares for Alice's position and invokes `metricOmmModifyLiquidityCallback` on Bob's contract.
8. Bob's contract transfers the required tokens to the pool.
9. Position shares are minted for Alice; Bob has deposited into the restricted pool without being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
