### Title
Pause guard applied only to `swap`, leaving `addLiquidity` and `removeLiquidity` unprotected — (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`MetricOmmPool` implements a three-level pause mechanism (`pauseLevel` 0/1/2) and a `whenNotPaused` modifier, but only applies that modifier to `swap`. Both `addLiquidity` and `removeLiquidity` execute unconditionally regardless of `pauseLevel`, so a pause triggered by a security incident does not prevent users from depositing principal into the compromised pool.

---

### Finding Description

`MetricOmmPool` declares a `pauseLevel` state variable and a `whenNotPaused` modifier:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 72-73
/// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol.
uint8 internal pauseLevel;
```

```solidity
// line 174-177
modifier whenNotPaused() {
    _checkNotPaused();   // reverts if pauseLevel != 0
    _;
}
```

The modifier is applied **only** to `swap`:

```solidity
// line 224
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ...
```

`addLiquidity` and `removeLiquidity` carry **no** pause check:

```solidity
// line 188
function addLiquidity(...) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...

// line 200
function removeLiquidity(...) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

When `pauseLevel` is set to 1 or 2 (e.g., because a price-oracle exploit or bin-accounting bug is discovered), swaps are blocked but liquidity operations proceed normally. Users who observe the pool is "paused" but do not understand the scope can still call `addLiquidity`, depositing token0/token1 into a pool whose invariants are already broken. Their principal is then exposed to the same vulnerability that caused the pause.

Additionally, `simulateSwapAndRevert` also lacks `whenNotPaused`:

```solidity
// line 314
function simulateSwapAndRevert(...) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) ...
```

Although this function ultimately reverts (rolling back state), it still invokes `_beforeSwap` and `_afterSwap` extension hooks, which may have external side-effects that are not rolled back if those extensions write to separate storage contracts.

---

### Impact Explanation

**Medium — direct loss of user principal.**

When a pool is paused due to a security incident (oracle manipulation, bin-accounting exploit, etc.), the intent is to freeze all fund-moving operations. Because `addLiquidity` is unguarded, users can deposit token0 and token1 into the compromised pool. If the pool is later unpaused without fully resolving the root cause, or if the attacker exploits the vulnerability before the pause is lifted, the newly deposited principal is at risk. The `addLiquidity` path transfers real tokens from the caller into the pool via the `LiquidityLib.addLiquidity` callback flow, so the loss is direct and not hypothetical.

---

### Likelihood Explanation

**Low-Medium.** A pause is an infrequent event, but when it occurs it is precisely because funds are at risk. The window between a pause being set and users learning about it (or understanding its limited scope) is realistic, especially for automated liquidity managers or routers that do not inspect `pauseLevel` before calling `addLiquidity`.

---

### Recommendation

Apply `whenNotPaused` to both `addLiquidity` and `removeLiquidity` (or at minimum to `addLiquidity`). If the protocol intentionally allows LP withdrawals during a pause (to let LPs exit), introduce a separate `whenNotFullyPaused` modifier that only blocks at `pauseLevel == 2`, and apply it to `removeLiquidity`, while applying the full `whenNotPaused` to `addLiquidity`.

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

---

### Proof of Concept

1. Pool is deployed with a mutable price provider.
2. Admin discovers the price provider is returning manipulated prices and calls `factory.setPause(pool, 1)` → `pauseLevel = 1`.
3. `swap` now reverts with `PoolPaused`.
4. A user (or automated LP manager) calls `addLiquidity` with a large `LiquidityDelta`. The call succeeds — no pause check exists — and tokens are transferred into the pool.
5. The attacker, aware the oracle is still manipulated, waits for the admin to unpause (or exploits a separate path that does not check `pauseLevel`), then drains the newly deposited tokens via a manipulated-price swap.
6. The user's deposited principal is lost.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-73)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
  uint8 internal pauseLevel;
  int8 internal curBinIdx;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
