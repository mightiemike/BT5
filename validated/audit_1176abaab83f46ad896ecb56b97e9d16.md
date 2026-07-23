Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` is intended to gate which addresses may add liquidity to a pool. However, it silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates `owner`, a free caller-supplied parameter. Because any caller can set `owner` to any allowlisted address, the allowlist is completely bypassed: an unauthorized depositor pays the tokens and injects liquidity while the extension approves the call based on the allowlisted `owner`.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded), then checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

Since `owner` is a free parameter in `addLiquidity(address owner, ...)`, any caller can pass an allowlisted address as `owner`. The extension sees `allowedDepositor[pool][allowlistedAddress] == true` and passes. The actual depositor (`msg.sender`) is never checked. The contract's own NatSpec ("Gates `addLiquidity` by depositor address") and the mapping name `allowedDepositor` confirm the intent was to check the actual depositor, not the position beneficiary.

`SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern — it checks `sender`, not the recipient:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

## Impact Explanation

Any address — including sanctioned, non-KYC'd, or otherwise restricted addresses — can add liquidity to a pool configured with `DepositAllowlistExtension`, completely nullifying the pool admin's access control. This is a confirmed **admin-boundary break**: an unprivileged path bypasses a pool-admin-configured guard. The unauthorized depositor pays the tokens and alters the pool's liquidity composition (bin totals, price curve), potentially harming existing LPs. The allowlisted `owner` receives LP shares they did not request.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known allowlisted address as `owner`. No special permissions, flash loans, or complex setup are needed. Allowlisted addresses are publicly observable via `AllowedToDepositSet` events. The bypass is immediately available to any observer whenever the extension is deployed and configured.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. `bob` is not allowlisted.
2. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
3. `MetricOmmPool` calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. The extension checks `allowedDepositor[pool][alice]` → `true` → passes. `bob`'s address is never evaluated.
5. `LiquidityLib.addLiquidity` credits the position to `alice`; the swap callback fires on `bob`, who pays the tokens.
6. `bob` has successfully deposited into a pool that was supposed to deny him, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
