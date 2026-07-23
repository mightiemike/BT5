### Title
Missing `whenNotPaused` Guard in `addLiquidity` Allows Deposits Into a Paused Pool — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.addLiquidity` is missing the `whenNotPaused` modifier that `swap` carries. When the pool is paused (e.g., due to an oracle failure or a security incident), swaps are correctly blocked, but new liquidity deposits are not, allowing users to send real tokens into a pool whose state is known to be unsafe.

---

### Finding Description

`MetricOmmPool` maintains a `pauseLevel` variable (0 = active, 1 = paused by admin, 2 = paused by protocol) and exposes a `whenNotPaused` modifier that reverts with `PoolPaused()` whenever `pauseLevel != 0`. [1](#0-0) [2](#0-1) [3](#0-2) 

`swap` correctly applies this guard:

```solidity
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ...
``` [4](#0-3) 

`addLiquidity` does not:

```solidity
function addLiquidity(...) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
``` [5](#0-4) 

The function proceeds to call `_beforeAddLiquidity`, `LiquidityLib.addLiquidity` (which pulls real tokens from the caller via the callback), and `_afterAddLiquidity` — all without ever checking `pauseLevel`. [6](#0-5) 

---

### Impact Explanation

A pool is paused precisely because its state is unsafe — a broken oracle feed, a detected price manipulation, or a protocol-level emergency. The pause is intended to freeze all fund-moving operations. Because `addLiquidity` bypasses this freeze, any user who deposits during a pause sends real `token0`/`token1` into a pool whose bin accounting or price provider is in a known-bad state. Those tokens are immediately at risk of being drained by the first swap that executes once the pool is unpaused, or by any attacker who can force an unpaused state. This is a direct loss of deposited principal.

---

### Likelihood Explanation

The `setPause` function is callable by the factory (admin or protocol), and the protocol explicitly documents two pause levels for different emergency scenarios. Any time either pause level is activated, the missing guard is reachable by any address that calls `addLiquidity` — no special role or precondition is required beyond the pool being paused. [7](#0-6) 

---

### Recommendation

Add `whenNotPaused` to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
```

`removeLiquidity` intentionally omitting the guard is defensible (LPs must be able to exit), but new deposits must be blocked while the pool is in a paused state.

---

### Proof of Concept

1. Pool is deployed with a mutable price provider; oracle begins returning a manipulated price.
2. Factory calls `pool.setPause(1)` to halt swaps.
3. Attacker (or unsuspecting LP) calls `pool.addLiquidity(...)` — no revert occurs; tokens are transferred in via the callback.
4. Factory calls `pool.setPause(0)` after believing the incident is resolved.
5. Attacker immediately calls `pool.swap(...)` against the now-unpaused pool, which prices the newly deposited liquidity at the manipulated oracle price, extracting the deposited tokens at a loss to the LP.

The root cause is identical in structure to the external report: a state flag (`pauseLevel`) that should gate an operation (`addLiquidity`) is never consulted, allowing a fund-moving action to proceed on an entity that has been administratively disabled. [8](#0-7)

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
