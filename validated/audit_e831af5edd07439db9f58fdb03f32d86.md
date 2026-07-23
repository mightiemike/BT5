Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Allowlist Bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is intended to gate `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument and enforces the allowlist only against `owner`. Because `owner` is a freely chosen parameter supplied by the caller, any non-allowlisted actor can bypass the guard by naming any allowlisted address as `owner` while themselves supplying the tokens and receiving the LP shares.

## Finding Description
`MetricOmmPool.addLiquidity` calls the extension hook passing `msg.sender` as `sender` and the caller-supplied `owner` as the second argument: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` discards the first argument and checks only `owner`: [2](#0-1) 

The actual token provider is `sender` (`msg.sender` in the pool), not `owner`. `LiquidityLib.addLiquidity` is called via DELEGATECALL, so `msg.sender` inside it is the original external caller. The library explicitly calls back to `msg.sender` to collect tokens: [3](#0-2) 

The library's own documentation confirms this: "Because every `public` function is called via DELEGATECALL from the pool: `msg.sender` is the original external caller." [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the direct caller), demonstrating the intended pattern: [5](#0-4) 

The parameter binding in `DepositAllowlistExtension` is inconsistent with `SwapAllowlistExtension` and wrong: it gates the wrong identity.

## Impact Explanation
A pool admin deploys `DepositAllowlistExtension` to restrict which addresses may supply liquidity (e.g., for KYC/regulatory compliance or LP composition control). Because the guard checks `owner` rather than `sender`, any non-allowlisted actor can call `pool.addLiquidity(owner=alice, ...)` where `alice` is any allowlisted address. The extension passes, the pool calls back to the non-allowlisted actor for token settlement, and LP shares are minted to `alice`. The allowlist provides zero protection against unauthorized depositors. This constitutes a broken core pool access-control mechanism causing the admin-boundary to be bypassed by an unprivileged path.

## Likelihood Explanation
Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA can call `pool.addLiquidity` directly with a chosen `owner`. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping: [6](#0-5) 

Likelihood is high.

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

`sender` is the address that will be called back to supply tokens and is therefore the correct identity to gate.

## Proof of Concept
```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false   // bob is NOT allowlisted

Attack:
  bob calls pool.addLiquidity(
      owner        = alice,   // allowlisted address
      salt         = 0,
      deltas       = <desired bins/shares>,
      callbackData = "",
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  sender = bob   (ignored — first arg discarded)
  owner  = alice (checked — allowlisted → passes)
  → no revert

Pool proceeds:
  LiquidityLib.addLiquidity calls IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(...) where msg.sender = bob
  bob supplies token0/token1
  LP shares minted to alice

Result:
  bob (non-allowlisted) has successfully deposited into the pool.
  DepositAllowlistExtension provided zero protection.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L16-18)
```text
/// @dev Because every `public` function is called via DELEGATECALL from the pool:
///      - `msg.sender` is the original external caller.
///      - `address(this)` is the pool contract.
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
