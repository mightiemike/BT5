Audit Report

## Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing allowlist bypass via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates only `owner`. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any caller to specify an arbitrary `owner`, an address not on the allowlist can deposit tokens into a restricted pool by naming any allowlisted address as `owner`. The extension never inspects the actual fund provider (`sender`), breaking the pool admin's access-control invariant.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both `sender` and `owner` to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first parameter but leaves it unnamed and unused, checking only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` independent of `msg.sender`, with `_validateOwner` only rejecting `address(0)`:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [4](#0-3) 

**Exploit path:** Alice (not allowlisted) calls `liquidityAdder.addLiquidityExactShares(pool, bob, ...)` where `bob` is allowlisted. The LiquidityAdder calls `pool.addLiquidity(bob, ...)`. The pool passes `msg.sender = LiquidityAdder` as `sender` and `bob` as `owner` to the hook. The extension checks `allowedDepositor[pool][bob]` — which is `true` — so the call succeeds. Alice's tokens (pulled from her in the callback) are deposited and Bob receives the LP shares. Alice never appears in any allowlist check.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates `sender` (the actual initiator), not `recipient`: [5](#0-4) 

The asymmetry confirms the deposit extension is checking the wrong identity.

## Impact Explanation

Any address not on the deposit allowlist can add liquidity to a restricted pool by routing through `MetricOmmPoolLiquidityAdder` and naming any allowlisted address as `owner`. The pool admin's access-control invariant — that only approved depositors may add liquidity — is broken. This constitutes an admin-boundary break: an unprivileged path through a legitimate periphery contract bypasses a configured pool guard. Consequences include unauthorized fund providers depositing into compliance-gated or KYC-restricted pools, and allowlisted users receiving unwanted LP positions they did not initiate.

## Likelihood Explanation

The attack requires only that the attacker knows one allowlisted address (observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` reads) and is willing to deposit their own tokens on that address's behalf. No privileged access, flash loans, or price manipulation is needed. `MetricOmmPoolLiquidityAdder` is a standard periphery contract intended for normal use, making this path trivially reachable.

## Recommendation

Gate the actual fund provider, not the position owner. Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the first parameter):

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

This mirrors the pattern already used by `SwapAllowlistExtension`, which correctly checks `sender`.

## Proof of Concept

```solidity
// Pool configured with DepositAllowlistExtension; only bob is allowlisted.
// alice is NOT allowlisted.

address alice = makeAddr("alice");
address bob   = makeAddr("bob");

// Admin allowlists bob only
depositAllowlist.setAllowedToDeposit(address(pool), bob, true);

// Alice tries direct deposit — correctly reverts
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
pool.addLiquidity(alice, 0, deltas, "", "");

// Alice routes through LiquidityAdder, naming bob as owner
deal(token0, alice, 1e18);
vm.startPrank(alice);
IERC20(token0).approve(address(liquidityAdder), type(uint256).max);

// Succeeds: extension checks owner=bob (allowlisted), ignores sender=alice
(uint256 a0, uint256 a1) = liquidityAdder.addLiquidityExactShares(
    address(pool), bob, 0, deltas, type(uint256).max, type(uint256).max, ""
);
vm.stopPrank();

// Bob now holds LP shares funded entirely by alice
uint256 bobShares = stateView.positionBinShares(address(pool), bob, 0, int8(4));
assertGt(bobShares, 0, "bob has shares");
assertEq(IERC20(token0).balanceOf(alice), 0, "alice paid");
// alice bypassed the deposit allowlist
```

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
