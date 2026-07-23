Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead gates on `owner`, a free caller-supplied parameter naming who will hold the resulting LP position. Any unprivileged address can bypass the allowlist by passing any allowlisted address as `owner`, completely defeating the pool admin's access-control gate. The asymmetry with `SwapAllowlistExtension`, which correctly checks `sender`, confirms this is a bug rather than a design choice.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the extension hook with the actual caller as the first argument:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both `sender` and `owner` verbatim to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and ignored; only `owner` is checked against the allowlist:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and ignores `recipient`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The wrong value checked is `allowedDepositor[pool][owner]` when it must be `allowedDepositor[pool][sender]`. Since `owner` is a free parameter supplied by the caller with no on-chain constraint tying it to `msg.sender`, the check is trivially bypassed.

## Impact Explanation

The deposit allowlist is the pool admin's mechanism to create a permissioned liquidity pool. With this bug the guard is entirely inoperative: any address can call `addLiquidity(owner = allowlisted_address, ...)`, the check passes, and the caller's tokens are pulled via the swap callback and credited to the allowlisted address's LP position. This constitutes an admin-boundary break with fund-impacting consequences: unauthorized parties can inject liquidity, diluting existing LP fee shares and violating the pool's intended access model.

## Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` directly on the pool with `owner` set to any allowlisted address. The allowlist provides zero protection against this call pattern. Likelihood is **High**.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor / caller of the pool) instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`. [3](#0-2) 

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. `bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       alice,   // owner — allowlisted, so the check passes
       0,       // salt
       deltas,
       callbackData,
       extensionData
   );
   ```
3. The extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
4. Bob's tokens are pulled via `metricOmmSwapCallback`; the LP position is minted under `alice`'s key.
5. Bob has successfully deposited into a pool he was explicitly barred from, bypassing the allowlist entirely.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
