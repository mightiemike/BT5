Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on position `owner` instead of actual depositor `sender`, allowing full allowlist bypass via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and gates only on `owner` (the position beneficiary). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` that is completely independent of `msg.sender` (the actual token payer), any address not on the allowlist can bypass the restriction by supplying an authorized user's address as `owner`. The pool admin's intent to restrict which addresses supply liquidity is fully defeated.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` into the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` discards the first parameter (`sender`) and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` that is not validated against `msg.sender`; `_validateOwner` only checks `owner != address(0)`:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

The internal `_addLiquidity` stores `msg.sender` as the token payer and passes the caller-supplied `owner` to the pool: [4](#0-3) 

`removeLiquidity` enforces `msg.sender == owner`, but `addLiquidity` has no such guard: [5](#0-4) 

The exploit path: Alice (not on allowlist) calls `addLiquidityExactShares(pool, Bob, ...)` where Bob is on the allowlist. The pool calls `_beforeAddLiquidity(LiquidityAdder, Bob, ...)`. The extension checks `allowedDepositor[pool][Bob]` → true → passes. The callback pulls tokens from Alice (the payer). Alice's tokens enter the pool credited to Bob's position, despite Alice not being on the allowlist.

`SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the direct caller), demonstrating the intended pattern: [6](#0-5) 

## Impact Explanation

The deposit allowlist is completely bypassable. Any address not on the allowlist can deposit tokens into a curated pool by calling `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, authorizedUser, ...)`. Unauthorized funds enter the pool's bin reserves, corrupting the curated LP composition the allowlist was designed to enforce. This constitutes a broken core pool access control mechanism and an admin-boundary break — the pool admin's ability to restrict liquidity providers is fully defeated by an unprivileged caller.

## Likelihood Explanation

Exploitation requires only knowing any one authorized depositor's address, which is trivially discoverable on-chain from past `addLiquidity` transactions or allowlist-set events. No special privilege, flash loan, or oracle manipulation is needed. Any user can execute this in a single transaction through the publicly deployed `MetricOmmPoolLiquidityAdder`.

## Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` must gate on `sender` (the actual caller/payer) rather than `owner` (the position beneficiary):

```solidity
// current (wrong actor — gates on position beneficiary)
function beforeAddLiquidity(address, address owner, ...)
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// fix (gate on the actual depositor/caller)
function beforeAddLiquidity(address sender, address owner, ...)
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  Alice  → NOT on allowlist
  Bob    → IS on allowlist (allowedDepositor[pool][Bob] = true)

Attack:
  Alice calls:
    MetricOmmPoolLiquidityAdder.addLiquidityExactShares(
        pool,
        Bob,    // owner = authorized address
        salt,
        deltas,
        maxAmount0,
        maxAmount1,
        ""
    )

Pool flow:
  pool.addLiquidity(Bob, salt, deltas, ...) called by LiquidityAdder
  → _beforeAddLiquidity(LiquidityAdder, Bob, ...)
  → DepositAllowlistExtension.beforeAddLiquidity(LiquidityAdder, Bob, ...)
      checks allowedDepositor[pool][Bob] → true → PASSES
  → LiquidityLib.addLiquidity credits Bob's position
  → callback pulls tokens from Alice (the payer)

Result:
  Alice's tokens enter the pool despite Alice not being on the allowlist.
  Bob receives shares Alice funded.
  The deposit allowlist is fully bypassed.

Foundry test: deploy pool with DepositAllowlistExtension, set Bob as allowed,
call addLiquidityExactShares from Alice with owner=Bob, assert success and
that Alice's token balance decreased.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
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
