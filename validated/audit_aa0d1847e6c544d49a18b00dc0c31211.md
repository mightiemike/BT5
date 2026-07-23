Audit Report

## Title
`addLiquidity()` and `removeLiquidity()` bypass the pool pause guard, allowing fund-moving operations on a paused pool — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool` implements a `whenNotPaused` modifier backed by a three-level `pauseLevel` state variable, but only `swap()` is protected by it. Both `addLiquidity()` and `removeLiquidity()` carry no pause check, so they remain fully operational when the pool is paused by the admin (`pauseLevel = 1`) or protocol owner (`pauseLevel = 2`). This allows token deposits into a compromised pool and potentially unfair withdrawals during a corrupted bin state.

## Finding Description

`MetricOmmPool` declares `pauseLevel` at [1](#0-0)  and a `whenNotPaused` modifier at [2](#0-1)  that reverts via `_checkNotPaused()` whenever `pauseLevel != 0`.

The factory exposes two pause entry points: `pausePool()` (pool admin, sets level 1) at [3](#0-2)  and `protocolPausePool()` (owner, sets level 2) at [4](#0-3) , both of which call `pool.setPause()`.

`swap()` correctly applies the guard: [5](#0-4) 

`addLiquidity()` carries only `nonReentrant` — no `whenNotPaused`: [6](#0-5) 

`removeLiquidity()` likewise carries only `nonReentrant` — no `whenNotPaused`: [7](#0-6) 

Neither function checks `pauseLevel` at any point. The full execution path — `_beforeAddLiquidity` / `LiquidityLib.addLiquidity` / `_afterAddLiquidity` and the equivalent remove hooks — runs to completion, transferring tokens in and out of the pool regardless of pause state.

## Impact Explanation

When the pool is paused in response to an emergency (oracle compromise, detected exploit, corrupted bin state), the operator's intent is to halt all fund-moving operations. Because `addLiquidity()` is unguarded, any caller can deposit token0/token1 into the paused, potentially compromised pool. The function also accepts an arbitrary `owner` address, so a router or multicall can silently route user funds into the paused pool without the user's awareness. Those tokens are immediately at risk once the pool is unpaused and arbitrageurs correct the price, constituting direct loss of user principal. Because `removeLiquidity()` is unguarded, in a scenario where bin accounting has been corrupted, a sophisticated LP could extract more than their fair share before the state is corrected. This satisfies the **Critical/High direct loss of user principal** impact gate.

## Likelihood Explanation

The pause mechanism is explicitly designed for emergency use. Any time the admin or protocol owner triggers a pause — a realistic and expected operational event — every call to `addLiquidity()` or `removeLiquidity()` bypasses the guard. No special attacker capability is required; any user or router interacting with the pool in the normal way will bypass the pause. The condition is trivially reachable by any unprivileged LP or router caller.

## Recommendation

Add `whenNotPaused` to both liquidity functions, mirroring the pattern already applied to `swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol

function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
-) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {

function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
-   nonReentrant(PoolActions.REMOVE_LIQUIDITY)
+   whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
```

## Proof of Concept

1. Deploy pool with a mutable price provider.
2. Oracle begins returning a manipulated price. Protocol owner calls `factory.protocolPausePool(pool)` → `pool.setPause(2)` → `pauseLevel = 2`.
3. Confirm `pool.swap()` reverts with `PoolPaused`.
4. Call `pool.addLiquidity(victim, salt, deltas, callbackData, extensionData)` directly. The function has no `whenNotPaused` check; it proceeds through `_beforeAddLiquidity`, `LiquidityLib.addLiquidity`, and `_afterAddLiquidity` without reverting, transferring victim's tokens into the compromised pool.
5. When the pool is unpaused after the oracle is corrected, the victim's position is immediately arbitraged at the corrected price, resulting in direct loss of principal.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L72-72)
```text
  uint8 internal pauseLevel;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L392-396)
```text
  function protocolPausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
    IMetricOmmPoolFactoryActions(pool).setPause(2);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-464)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }
```
