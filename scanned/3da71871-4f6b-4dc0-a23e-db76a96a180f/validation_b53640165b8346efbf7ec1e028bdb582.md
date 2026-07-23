### Title
Pool Pause Mechanism Covers Only `swap`, Leaving `addLiquidity` and `removeLiquidity` Unguarded — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool` exposes a `pauseLevel` state variable, a `whenNotPaused` modifier, and a factory-callable `setPause` setter. However, only `swap` is decorated with `whenNotPaused`. The two other user-accessible fund-moving functions — `addLiquidity` and `removeLiquidity` — carry no pause guard, so they execute freely regardless of `pauseLevel`. This is the direct structural analog of the WithdrawQueue M-02 finding: a pause mechanism is wired up and callable by the administrator, but the user-facing functions that move principal are never gated by it.

---

### Finding Description

`MetricOmmPool` declares:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 72
uint8 internal pauseLevel;   // 0 = active, 1 = paused by admin, 2 = paused by protocol

modifier whenNotPaused() {   // lines 174-177
    _checkNotPaused();
    _;
}
function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
}
```

The factory can raise `pauseLevel` at any time via `setPause`:

```solidity
// line 455-461
function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    ...
    pauseLevel = newLevel;
}
```

`swap` correctly honours the guard:

```solidity
// line 224
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ...
```

But `addLiquidity` and `removeLiquidity` do not:

```solidity
// line 182-196
function addLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas,
    bytes calldata callbackData, bytes calldata extensionData)
    external nonReentrant(PoolActions.ADD_LIQUIDITY)   // ← no whenNotPaused
    returns (uint256 amount0Added, uint256 amount1Added) { ... }

// line 199-212
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas,
    bytes calldata extensionData)
    external nonReentrant(PoolActions.REMOVE_LIQUIDITY) // ← no whenNotPaused
    returns (uint256 amount0Removed, uint256 amount1Removed) { ... }
```

Both functions transfer real token balances: `addLiquidity` pulls tokens from the caller via `metricOmmModifyLiquidityCallback`, and `removeLiquidity` returns the LP's proportional share of `binTotals.scaledToken0` / `scaledToken1` to the owner.

---

### Impact Explanation

When the administrator raises `pauseLevel` to 1 or 2 — the intended emergency stop — the following remains fully operational for any unprivileged caller:

1. **`removeLiquidity`**: LPs can drain their proportional share of every bin's token balances out of the pool. If the pool was paused mid-exploit (e.g., after swaps at a manipulated oracle price have already skewed bin balances), LPs who exit during the pause receive the corrupted distribution rather than the fair one the admin intended to preserve while investigating or restoring state. The admin's ability to freeze the pool's asset composition — a prerequisite for any on-chain remediation — is broken.

2. **`addLiquidity`**: New deposits continue to flow in, altering `binTotals` and per-bin share accounting while the pool is supposed to be inert. Any extension-based stop-loss or velocity guard that reads post-swap bin state (e.g., `OracleValueStopLossExtension`) will see a different pool composition than the one that existed at pause time, potentially resetting high-watermarks or skewing future guard thresholds once the pool is unpaused.

The net result: the administrator cannot halt LP principal flows in an emergency, directly contradicting the two-level pause design ("paused by admin", "paused by protocol") and exposing LP assets to continued movement during a period when the protocol assumes the pool is frozen.

---

### Likelihood Explanation

- Any LP (unprivileged) can call `removeLiquidity` at any time, including while `pauseLevel != 0`.
- The `MetricOmmPoolLiquidityAdder` periphery contract also calls `pool.addLiquidity` directly; it performs no pause check of its own, so the bypass is reachable through the standard user-facing periphery as well.
- The trigger requires the admin to have paused the pool, but once paused the bypass is automatic and requires no special knowledge or coordination.

---

### Recommendation

Add `whenNotPaused` to both functions, mirroring the pattern already applied to `swap`:

```diff
- function addLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas,
-     bytes calldata callbackData, bytes calldata extensionData)
-     external nonReentrant(PoolActions.ADD_LIQUIDITY)
+ function addLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas,
+     bytes calldata callbackData, bytes calldata extensionData)
+     external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY)

- function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas,
-     bytes calldata extensionData)
-     external nonReentrant(PoolActions.REMOVE_LIQUIDITY)
+ function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas,
+     bytes calldata extensionData)
+     external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY)
```

If the protocol intentionally allows LP exits during a swap-only pause, a separate `pauseLevel`-aware modifier (e.g., `whenNotFullyPaused`) should be introduced and documented, so the distinction is explicit rather than accidental.

---

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension` and `SwapAllowlistExtension`. Oracle price is manipulated 5× above fair value.
2. Attacker executes several `zeroForOne` swaps (token0 in, token1 out) at the inflated price, draining token1 from bins. Stop-loss has not yet triggered because the HWM has not been set.
3. Admin detects the drain and calls `factory.setPause(pool, 1)` → `pool.setPause(1)`. `pauseLevel = 1`. Swaps now revert with `PoolPaused`.
4. Admin intends to restore bin balances (e.g., by injecting token1) before allowing further activity.
5. **Before the admin can act**, any LP calls `pool.removeLiquidity(owner, salt, deltas, "")`. The call succeeds — `whenNotPaused` is absent — and the LP withdraws their proportional share of the already-drained bins, receiving far less token1 than they held before the exploit. The pool's asset composition changes while the admin believed it was frozen.
6. The admin's remediation (token injection) now targets a different pool state than intended, potentially over- or under-compensating remaining LPs. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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
