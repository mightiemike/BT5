Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing deposit allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and checks `owner` (the LP-position recipient) instead. Any address not on the allowlist can bypass the guard by calling `addLiquidity` with an allowlisted address as `owner`, paying tokens themselves while LP shares are minted to the allowlisted address.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-position recipient:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both values verbatim to every configured extension:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first `address` argument but names it `_` (unnamed) and never reads it. The allowlist check is performed only on `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

After the hook passes, `LiquidityLib.addLiquidity` issues the token-transfer callback to `msg.sender` (the original external caller, confirmed by the DELEGATECALL comment in `LiquidityLib.sol` L17: *"`msg.sender` is the original external caller"*):

```solidity
// LiquidityLib.sol L147-148
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

LP shares are keyed by `_positionBinKey(owner, salt, binIdx)` and credited to `owner`. The attacker pays tokens; the allowlisted address receives the position.

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` instead of `recipient`, confirming the asymmetry is a bug, not a design choice.

## Impact Explanation

An unauthorized address can deposit into a pool protected by `DepositAllowlistExtension` by supplying any allowlisted address as `owner`. The pool admin's primary access-control mechanism for liquidity is fully bypassed. This constitutes an admin-boundary break: an unprivileged path circumvents the pool admin's configured guard, allowing unauthorized parties to alter pool composition and bin balances at will. All allowlisted addresses are publicly readable on-chain via `allowedDepositor` events or storage, making target selection trivial.

## Likelihood Explanation

Exploitation requires only: (1) a pool with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`, and (2) knowledge of one allowlisted address — trivially obtained by reading `allowedDepositor` events or storage. No special role, flash loan, or oracle manipulation is needed. Any EOA or contract implementing `IMetricOmmModifyLiquidityCallback` can trigger this in a single transaction.

## Recommendation

Rename the first parameter and check `sender` instead of `owner`, mirroring `SwapAllowlistExtension.beforeSwap`:

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` in `BEFORE_ADD_LIQUIDITY_ORDER`; allowlists only `alice` via `setAllowedToDeposit(pool, alice, true)`.
2. `bob` (not allowlisted) deploys a contract implementing `IMetricOmmModifyLiquidityCallback` and calls:
   ```solidity
   pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData);
   ```
3. Pool calls `extension.beforeAddLiquidity(sender=bob, owner=alice, …)`.
4. Extension ignores `bob`; checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` issues `metricOmmModifyLiquidityCallback` to `bob`; `bob` pays tokens; LP shares are minted to `alice` (keyed by `_positionBinKey(alice, salt, binIdx)`).
6. `bob` has successfully deposited into a pool he is not authorized to access.

**Foundry test sketch:**
```solidity
// Deploy pool with DepositAllowlistExtension, allowlist alice only
// bob calls pool.addLiquidity(alice, salt, deltas, "", "")
// Assert: no revert, alice's positionBinShares > 0, bob paid tokens
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
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
