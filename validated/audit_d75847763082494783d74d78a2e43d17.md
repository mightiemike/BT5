Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` validates LP position `owner` instead of actual depositor `sender`, allowing allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates `owner`, a free caller-supplied parameter naming the LP share recipient. Because any caller can nominate an allowlisted address as `owner`, the deposit allowlist is completely nullified: any unprivileged address can deposit into a pool configured to restrict deposits.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as a separate argument to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully encodes both actors and forwards them to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but names the first parameter `_` (anonymous) and checks only `owner`: [3](#0-2) 

The parallel `SwapAllowlistExtension.beforeSwap` correctly names and validates `sender`: [4](#0-3) 

The asymmetry is the root cause. Because `owner` is a free parameter with no constraint tying it to `msg.sender` in `addLiquidity`, any caller can pass an allowlisted address as `owner`, causing the guard to evaluate `allowedDepositor[pool][allowlistedAddress] == true` and pass. The pool then issues a `metricOmmSwapCallback` to the actual `msg.sender` (the unauthorized caller) to collect tokens, and LP shares are minted to the allowlisted `owner`. No existing guard prevents this: `addLiquidity` has no `msg.sender == owner` check (unlike `removeLiquidity` which does enforce `msg.sender != owner` revert). [5](#0-4) 

## Impact Explanation

This is an admin-boundary break: the pool admin's sole deposit restriction mechanism is completely bypassed by an unprivileged path. Any actor can inject liquidity into a pool that is supposed to be closed to them. Secondary consequences include unauthorized actors shifting bin balances and `curPosInBin`, altering the marginal price seen by subsequent swaps and harming existing LPs.

## Likelihood Explanation

Exploitation requires only knowing one allowlisted address (observable on-chain via `AllowedToDepositSet` events or direct mapping reads at `allowedDepositor`) and holding enough token0/token1 to satisfy the callback. No privileged role, flash loan, or special setup is needed. Any EOA or contract can execute this in a single transaction, making it trivially repeatable.

## Recommendation

Replace the anonymous first parameter with `sender` and validate it, mirroring `SwapAllowlistExtension`:

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

Also update `isAllowedToDeposit` NatDoc and the admin setter documentation to clarify that the controlled address is the **caller of `addLiquidity`**, not the LP position owner.

## Proof of Concept

```
Setup
─────
Pool P configured with DepositAllowlistExtension E.
allowedDepositor[P][Alice] = true
allowedDepositor[P][Bob]   = false  // Bob is blocked

Attack
──────
Bob calls:
  P.addLiquidity(
      owner         = Alice,   // allowlisted — passes the guard
      salt          = 0,
      deltas        = <desired position>,
      callbackData  = ...,
      extensionData = ""
  )

Extension hook receives:
  beforeAddLiquidity(sender=Bob, owner=Alice, ...)
  → checks allowedDepositor[P][Alice] == true  ✓
  → hook returns selector, no revert

Pool issues metricOmmSwapCallback to Bob (msg.sender).
Bob transfers tokens; Alice receives LP shares.

Result: Bob deposited into a pool he is explicitly barred from.
        The allowlist invariant is broken.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
