Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks caller-supplied `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token payer, i.e., `msg.sender` of the original `addLiquidity` call) and gates only on `owner`, which is a freely caller-supplied argument with no on-chain constraint. Any non-allowlisted address can bypass the deposit gate by nominating an allowlisted address as `owner`, causing unauthorized capital to enter the pool and minting LP shares to an address that never consented to the deposit.

## Finding Description

`MetricOmmPool.addLiquidity` accepts `owner` as a plain caller-supplied argument and passes `msg.sender` as `sender` to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` correctly forwards both values to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender` (unnamed first parameter) and checks only `owner`:

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

`msg.sender` inside the extension is the pool (correct for pool-identity keying of the allowlist mappings). `owner` is whatever the original caller passed. Since `addLiquidity` has no `msg.sender == owner` guard (unlike `removeLiquidity` which enforces `if (msg.sender != owner) revert NotPositionOwner()`), any caller can supply an arbitrary `owner`.

The standard periphery path makes this trivially reachable: `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` stores `msg.sender` as the payer in transient context and forwards the caller-chosen `owner` directly to the pool. The extension sees an allowlisted `owner` while the actual token pull comes from the non-allowlisted `msg.sender` (payer):

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L65-67
_validateOwner(owner);
_validateDeltas(deltas);
return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper) rather than `recipient`, confirming the asymmetry is a defect specific to the deposit extension:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-38
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

## Impact Explanation

The deposit allowlist is the sole on-chain mechanism preventing unauthorized addresses from providing liquidity to a restricted pool. With the guard checking the wrong identity:

1. A non-allowlisted address deposits tokens into the pool under an allowlisted `owner`, fully bypassing the access control.
2. The allowlisted address receives LP shares it never authorized; between deposit and removal, adverse pool price movement can cause real loss to that address.
3. The pool admin's deposit restriction is completely defeated at zero protocol cost by any unprivileged caller.

This constitutes a broken core pool functionality (admin-boundary break: the deposit allowlist cap is bypassed by an unprivileged path) with direct fund impact on the allowlisted LP.

## Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` directly on the pool or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an arbitrary `owner`. The only cost is the token amount deposited, which is recoverable if the attacker controls the allowlisted address. The path is reachable both directly on the pool and through the standard periphery contract.

## Recommendation

Replace the unnamed (ignored) first parameter with `sender` and gate on it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][ALICE] = true   // ALICE is allowlisted
  BOB is NOT allowlisted

Attack via MetricOmmPoolLiquidityAdder (BOB calls):
  adder.addLiquidityExactShares(
      pool  = pool,
      owner = ALICE,   // allowlisted → check passes
      salt  = 0,
      deltas = <shares>,
      maxAmountToken0 = X,
      maxAmountToken1 = Y,
      extensionData = ""
  )

Call chain:
  adder stores payer = BOB in transient context
  pool.addLiquidity(owner=ALICE, ...) called by adder
  _beforeAddLiquidity(sender=adder, owner=ALICE, ...)
  extension: allowedDepositor[pool][ALICE] == true → no revert
  LiquidityLib mints shares to (ALICE, 0) position key
  callback pulls tokens from BOB (the stored payer)

Result:
  BOB has bypassed the deposit allowlist
  ALICE holds LP shares she never authorized
  Unauthorized capital has entered the restricted pool
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
