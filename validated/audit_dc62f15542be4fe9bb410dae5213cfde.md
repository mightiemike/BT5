Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by "depositor address, per pool," but its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of the pool call) and instead validates the caller-controlled `owner` argument. Because `addLiquidity` imposes no constraint that `owner == msg.sender`, any non-allowlisted address can bypass the gate by supplying an allowlisted address as `owner`, permanently altering pool token composition and diluting existing LP shares.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded) and gates on `owner` instead: [3](#0-2) 

`addLiquidity` has no constraint that `owner == msg.sender`. The only owner validation in the periphery router `MetricOmmPoolLiquidityAdder._validateOwner` only rejects `address(0)`: [4](#0-3) 

The sister extension `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper) and ignores `recipient`, establishing the correct pattern: [5](#0-4) 

**Exploit path:** Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` where Alice is allowlisted. The pool calls `_beforeAddLiquidity(bob, alice, ...)`. The extension discards `bob`, checks `allowedDepositor[pool][alice]` → `true` → no revert. Bob's tokens are pulled via callback; the LP position is minted to Alice. Bob has successfully deposited into a pool that was supposed to block him.

Existing guards are insufficient: `removeLiquidity` enforces `msg.sender == owner` but `addLiquidity` does not. The `_validateOwner` check only rejects `address(0)`. Allowlisted addresses are publicly visible on-chain via `AllowedToDepositSet` events.

## Impact Explanation

A non-allowlisted address can freely alter pool token composition and bin state in a pool configured with `DepositAllowlistExtension`. The deposited tokens are permanently locked in the allowlisted address's LP position (the attacker cannot reclaim them since `removeLiquidity` enforces `msg.sender == owner`), but the pool's `binTotals` and bin share accounting are permanently modified. Existing LPs suffer dilution of their proportional claim on pool assets — a direct reduction in the value of their LP shares. For compliance-gated or institutional pools, this constitutes a broken core invariant: the deposit allowlist fails to restrict who can alter pool state, which is its sole purpose.

## Likelihood Explanation

No privileged access, flash loan, or special setup is required. Allowlisted addresses are publicly observable on-chain via `AllowedToDepositSet` events. A single direct call to `pool.addLiquidity` suffices. The only cost to the attacker is the token amount deposited, which is permanently locked in the allowlisted address's LP position. The attack is repeatable by any actor at any time.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

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

This ensures the gate validates the actual caller of `addLiquidity`, not the caller-controlled position-owner parameter.

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a configured extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` directly.
4. Pool calls `_beforeAddLiquidity(bob, alice, ...)` → extension receives `sender=bob` (discarded), `owner=alice`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's tokens are pulled via callback; the LP position is minted to Alice.
7. Bob has successfully deposited into a pool that was supposed to block him. Pool `binTotals` and existing LP share values are altered without authorization.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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
