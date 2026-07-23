Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position Owner Instead of Actual Depositor, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller who pays tokens via the modify-liquidity callback) and validates only `owner` (the position recipient) against the allowlist. Any unprivileged address can bypass the deposit allowlist by calling `addLiquidity` with an allowlisted `owner` address, depositing liquidity into a restricted pool without authorization.

## Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct address roles: `msg.sender` (the actual caller, who pays tokens via `metricOmmModifyLiquidityCallback`) and `owner` (the address that receives LP position shares). [1](#0-0) 

Both are forwarded to `_beforeAddLiquidity`: [2](#0-1) 

`LiquidityLib.addLiquidity` confirms that the callback — and therefore token payment — is invoked on `msg.sender` (the actual caller), not `owner`: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded). It only checks `owner`: [4](#0-3) 

The check `allowedDepositor[msg.sender][owner]` passes whenever `owner` is on the allowlist, regardless of who `sender` is. The actual depositor — the address paying tokens — is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper): [5](#0-4) 

The contract's own NatSpec states the intent: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is `sender`, not `owner`: [6](#0-5) 

## Impact Explanation

A pool admin deploys `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC'd institutional LPs). Any unprivileged address can bypass this control by specifying an allowlisted address as `owner`. The unauthorized caller pays tokens via the callback; the allowlisted address receives the LP position. The pool's core access control invariant is broken: unauthorized parties can inject liquidity into restricted pools, manipulating bin balances and pool state in ways the pool admin did not authorize. This is a direct admin-boundary break via an unprivileged path.

## Likelihood Explanation

Exploitation requires no special privileges, no flash loans, and no complex setup. Any address can call `addLiquidity` on a pool with this extension configured, specifying any allowlisted address as `owner`. The allowlisted addresses are publicly readable from `allowedDepositor`. The attack is repeatable at will.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/caller) instead of `owner`:

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
```

If the intent is to gate both the depositor and the position owner, both should be checked independently.

## Proof of Concept

```
Setup:
  - Pool P has DepositAllowlistExtension configured
  - allowedDepositor[P][alice] = true
  - bob is NOT on the allowlist

Attack:
  1. bob calls P.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)
  2. Extension checks allowedDepositor[P][alice] → true → no revert
  3. LiquidityLib calls metricOmmModifyLiquidityCallback on bob (msg.sender of pool) to collect tokens
  4. bob pays tokens; alice receives LP shares
  5. bob has successfully deposited into a restricted pool without being allowlisted
```

Foundry test plan: deploy a pool with `DepositAllowlistExtension`, allowlist `alice`, call `addLiquidity` from `bob` with `owner=alice`, assert the call succeeds and `bob`'s token balance decreases while `alice`'s position shares increase.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-194)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
