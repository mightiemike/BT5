Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and validates only `owner` (a freely chosen LP position recipient). Because `owner` is caller-supplied and `allowedDepositor` is a public mapping, any address excluded from the allowlist can bypass the guard by nominating any allowlisted address as `owner`. The pool admin's access-control intent is fully circumvented.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes and forwards both in order `(sender, owner, ...)` to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter unnamed (`address,`), discarding `sender` entirely, and checks only `owner`: [3](#0-2) 

The contract NatSpec states it "Gates `addLiquidity` by depositor address", but the actual check is on the LP position recipient, not the depositor. The `allowedDepositor` mapping is `public`, making all allowlisted addresses trivially discoverable on-chain: [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), not `recipient`: [5](#0-4) 

The structural mismatch is exact: `DepositAllowlistExtension` validates a sub-component of the call (`owner`) but never binds that check back to the entity the guard is supposed to constrain (`sender`/depositor).

## Impact Explanation

An address not on the allowlist can call `pool.addLiquidity(owner = <any_allowlisted_address>, ...)`. The extension evaluates `allowedDepositor[pool][allowlisted_address]` → `true` → no revert. `LiquidityLib.addLiquidity` credits LP shares to the allowlisted address while tokens are pulled from the unauthorized caller via the swap callback. The pool admin's access-control boundary — restricting which parties may inject liquidity — is fully bypassed. This constitutes an admin-boundary break: an admin-configured guard is circumvented by an unprivileged path, allowing unauthorized parties to alter pool bin balances, affect stop-loss watermarks, and manipulate pool state in ways the admin explicitly intended to prevent. [6](#0-5) 

## Likelihood Explanation

The `allowedDepositor` mapping is `public`, so any allowlisted address is trivially discoverable on-chain with a single storage read or event scan. The bypass requires only a single `addLiquidity` call with `owner` set to any allowlisted address. No special privileges, flash loans, or multi-step setup are needed. Any actor blocked by the allowlist can immediately and repeatably circumvent it. [7](#0-6) 

## Recommendation

Replace the `owner` check with a `sender` check, mirroring the correct pattern in `SwapAllowlistExtension`:

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
``` [3](#0-2) 

## Proof of Concept

1. Deploy pool with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) reads `allowedDepositor` public mapping to discover Alice's address.
4. Bob calls `pool.addLiquidity(owner = alice, salt = 0, deltas = ..., callbackData = ..., extensionData = ...)`.
5. Pool calls `_beforeAddLiquidity(msg.sender=Bob, owner=alice, ...)` → extension receives `(Bob, alice, ...)`.
6. Extension discards `Bob`, evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
7. `LiquidityLib.addLiquidity` credits LP shares to `alice`; tokens are pulled from Bob via the swap callback.
8. Bob has successfully deposited into the allowlisted pool despite being explicitly excluded. [8](#0-7) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
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
