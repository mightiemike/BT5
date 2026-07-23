Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on LP-share recipient (`owner`) instead of actual depositor (`sender`), fully defeating the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token-providing caller) and checks `owner` (the LP-share recipient) instead. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any non-allowlisted address can name an allowlisted address as `owner` and deposit freely, completely defeating the admin-configured access-control boundary.

## Finding Description
`MetricOmmPool.addLiquidity` dispatches the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

So `sender = msg.sender` (the actual depositor who will pay tokens) and `owner` is the LP-share recipient. The extension implementation discards `sender` entirely:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first parameter (`sender`) is unnamed and discarded. Only `owner` is checked against `allowedDepositor`. Meanwhile, `addLiquidity` has no `msg.sender == owner` guard: [3](#0-2) 

Token payment is pulled from `msg.sender` (the actual caller) inside `LiquidityLib.addLiquidity` via the modify-liquidity callback:

```solidity
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
``` [4](#0-3) 

The allowlist check therefore validates the wrong principal: it approves the LP-share recipient while the actual token provider is never verified. By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and discards `recipient`: [5](#0-4) 

The recovery path is confirmed: `removeLiquidity` enforces `msg.sender == owner`, so a colluding allowlisted owner can return the tokens to the actual depositor, making the bypass economically free: [6](#0-5) 

## Impact Explanation
This is a direct admin-boundary break. The deposit allowlist is an admin-configured access-control boundary (documented as "Gates `addLiquidity` by depositor address, per pool"). Any unprivileged address can bypass it entirely by supplying an allowlisted address as `owner`. The bypassing address injects tokens into restricted pools, forces the pool to accept liquidity from actors the pool admin explicitly excluded (for regulatory, risk, or operational reasons), and can recover deposited tokens via a colluding allowlisted owner calling `removeLiquidity`, making the bypass economically free. The exact corrupted value is `allowedDepositor[pool][owner]` being used as the gate instead of `allowedDepositor[pool][sender]`, causing the extension decision to be wrong for every call where `msg.sender != owner`.

## Likelihood Explanation
Requires no special role or privilege — any EOA or contract can call `addLiquidity` with an arbitrary `owner`. Allowlisted owner addresses are discoverable on-chain via emitted `AllowedToDepositSet` events. The bypass is a single direct call with no flash loan or multi-step setup required. It is repeatable indefinitely.

## Recommendation
Check `sender` (the actual depositor/caller) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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
1. Pool admin deploys a pool with `DepositAllowlistExtension` configured.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — Alice is allowlisted; Bob is not.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. `_beforeAddLiquidity(bob, alice, ...)` is dispatched; the extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` pulls tokens from Bob via `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)`.
6. Alice's position is credited with LP shares.
7. Alice calls `removeLiquidity` (passes `msg.sender == owner` check) and transfers the tokens back to Bob.

Bob has deposited into a restricted pool and recovered his funds — the allowlist provided zero protection.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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
