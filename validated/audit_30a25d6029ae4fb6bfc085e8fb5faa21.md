Audit Report

## Title
`addLiquidity()` Bypasses `whenNotPaused` Guard, Allowing Deposits Into a Paused Pool — (File: `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool` defines a `whenNotPaused` modifier backed by `_checkNotPaused()` that reverts when `pauseLevel != 0`. This modifier is applied to `swap()` but is absent from `addLiquidity()`. When the pool is paused via `setPause()`, any LP can still deposit principal into a pool that is in a compromised or halted state, with no on-chain signal that the deposit path remains open.

## Finding Description
`pauseLevel` is declared at [1](#0-0)  and the guard is implemented at [2](#0-1)  via the `whenNotPaused` modifier at [3](#0-2) .

`swap()` correctly carries the modifier: [4](#0-3) 

`addLiquidity()` carries only `nonReentrant` — no pause check: [5](#0-4) 

`setPause()` is callable by the factory and sets `pauseLevel` to 1 or 2: [6](#0-5) 

The exploit path is: factory pauses pool → `swap()` reverts with `PoolPaused` → `addLiquidity()` succeeds, transferring tokens into the compromised pool. The `_beforeAddLiquidity` extension hook (e.g., a deposit allowlist) is still invoked, but it does not substitute for the pause check and cannot be assumed to block deposits in all configurations.

## Impact Explanation
An LP depositing during a pause window transfers tokens into a pool that the admin has flagged as unsafe. Those tokens are immediately subject to whatever condition triggered the pause (e.g., a manipulated oracle price, an active exploit, or an accounting inconsistency). This constitutes direct loss of user principal — a Critical/High impact under the allowed impact gate ("direct loss of user principal" and "broken core pool functionality causing loss of funds").

## Likelihood Explanation
The trigger is a semi-trusted admin pause, which occurs precisely when risk is highest. The victim path requires no privilege — any LP or integrator calling `addLiquidity()` during the pause window is affected. The asymmetry (swaps blocked, deposits open) is invisible on-chain to callers who only observe `swap()` reverting. The condition is repeatable for every pause event.

## Recommendation
Apply `whenNotPaused` to `addLiquidity()`:

```solidity
function addLiquidity(
    address owner, uint80 salt, LiquidityDelta calldata deltas,
    bytes calldata callbackData, bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

If the design intent is to allow LP exits during a pause, `removeLiquidity()` can remain unguarded, but new deposits must be blocked.

## Proof of Concept
1. Deploy pool; factory calls `setPause(pool, 1)` — `pauseLevel` becomes 1.
2. Call `swap(...)` — reverts with `PoolPaused`. Confirmed paused.
3. Call `addLiquidity(owner, salt, deltas, callbackData, extensionData)` — **succeeds**, tokens transferred in (no `whenNotPaused` check on this path).
4. Pool is in the compromised state that triggered the pause; deposited tokens are immediately at risk.
5. After pause lifts, an attacker executes a favorable swap against the newly deposited liquidity, or the underlying condition (e.g., oracle correction) drains the deposited tokens through bin accounting.

Minimal Foundry test: set `pauseLevel = 1` via `setPause`, assert `swap` reverts, assert `addLiquidity` with nonzero shares succeeds and token balances of the pool increase.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-72)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L455-461)
```text
  function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    if (newLevel == pauseLevel) return;
    uint8 prev = pauseLevel;
    pauseLevel = newLevel;
    emit PauseLevelUpdated(prev, newLevel);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
