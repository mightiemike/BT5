Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Share Recipient (`owner`) Instead of Token Payer (`sender`), Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that pays tokens via the liquidity callback) and gates only on `owner` (the caller-supplied share recipient). Because `owner` is a free parameter with no constraint tying it to the actual payer, any unallowlisted address can satisfy the check by passing an allowlisted address as `owner`, pay the tokens itself, and complete the deposit — fully bypassing the access-control boundary the allowlist was configured to enforce.

## Finding Description

`MetricOmmPool.addLiquidity` passes two distinct identities into the extension hook chain:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is forwarded as `sender` — the address that will be called back to pay tokens — and `owner` is the caller-supplied share recipient. This is confirmed by `LiquidityLib.addLiquidity`, which issues the token-pull callback to `msg.sender` of the pool call:

```solidity
// metric-core/contracts/libraries/LiquidityLib.sol L147-148
IMetricOmmModifyLiquidityCallback(msg.sender)
  .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

`ExtensionCalling._beforeAddLiquidity` correctly encodes both arguments and forwards them to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
  BEFORE_ADD_LIQUIDITY_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` entirely (unnamed `address` in position 1) and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The contract's own NatSpec declares its purpose as gating `addLiquidity` **by depositor address**, and the storage mapping is named `allowedDepositor`. The depositor — the entity that transfers tokens into the pool — is `sender`, not `owner`. Since `owner` is freely chosen by the caller, the guard is bound to the wrong identity and is trivially bypassed.

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., regulatory compliance, KYC gating, controlled LP composition). With this bug, an unallowlisted address calls `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`. The extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → no revert. The unallowlisted address pays the tokens via the liquidity callback; the allowlisted address receives the LP shares. The pool now holds liquidity sourced from an unallowlisted depositor, violating the admin-boundary the allowlist was meant to enforce. This is a direct admin-boundary break: an unprivileged path bypasses a configured access-control guard.

## Likelihood Explanation

The trigger requires no special privilege. Any externally-owned address can call `pool.addLiquidity` directly with an arbitrary `owner`. The only prerequisite is knowing at least one allowlisted address for the target pool, which is publicly readable via the `allowedDepositor` mapping or observable on-chain. The bypass is reachable by any actor at any time the pool is live and requires no flash loan, reentrancy, or privileged role.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the first argument, the token payer) instead of `owner` (the second argument, the share recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` wired into `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` and leaves `bob` unallowlisted.
3. `bob` (unallowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. `_beforeAddLiquidity(bob, alice, ...)` is called; extension receives `(bob, alice, ...)`.
5. Extension ignores `bob` (first arg) and evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` calls `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)` — `bob` pays the tokens; `alice` receives the LP shares.
7. `bob` has successfully deposited into the pool despite being explicitly excluded from the allowlist.

**Foundry test sketch:**
```solidity
// bob is not allowlisted; alice is
vm.prank(bob);
pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
// assert: no revert, bob paid tokens, alice holds shares
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-14)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
