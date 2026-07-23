### Title
`addLiquidity()` and `removeLiquidity()` bypass the pool pause guard, allowing fund-moving operations on a paused pool — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool` implements a three-level pause system (`pauseLevel` 0/1/2) with a `whenNotPaused` modifier, but only `swap()` is guarded by it. `addLiquidity()` and `removeLiquidity()` carry no pause check, so both functions remain fully operational when the pool is paused by the admin or protocol.

### Finding Description

`MetricOmmPool` declares `pauseLevel` and a `whenNotPaused` modifier that reverts when `pauseLevel != 0`: [1](#0-0) [2](#0-1) [3](#0-2) 

The factory exposes `pausePool()` (admin, sets level 1) and `protocolPausePool()` (owner, sets level 2), both of which call `pool.setPause()`: [4](#0-3) [5](#0-4) 

`swap()` correctly carries the guard: [6](#0-5) 

But `addLiquidity()` and `removeLiquidity()` do not: [7](#0-6) [8](#0-7) 

Neither function checks `pauseLevel` at any point. The `_beforeAddLiquidity` / `_afterAddLiquidity` extension hooks are still invoked, and `LiquidityLib.addLiquidity` / `LiquidityLib.removeLiquidity` execute fully, transferring tokens in and out of the pool.

### Impact Explanation

When the pool is paused in response to an emergency (oracle compromise, detected exploit, corrupted bin state), the operator's intent is to halt all fund-moving operations. Because `addLiquidity()` is unguarded:

- Any caller can deposit token0/token1 into the paused, potentially compromised pool. Those tokens are immediately at risk once the pool is unpaused and arbitrageurs correct the price.
- The `addLiquidity()` signature accepts an arbitrary `owner` address, so the depositor need not be the position owner — a router or multicall can silently route user funds into the paused pool.

Because `removeLiquidity()` is unguarded:

- Existing LPs can withdraw during a pause. While this may seem benign, it breaks the invariant that the pool is frozen, and in a scenario where bin accounting has been corrupted, a sophisticated LP could extract more than their fair share before the state is corrected.

Direct loss of user principal (depositing into a compromised pool) satisfies the contest-relevant impact threshold.

### Likelihood Explanation

The pause mechanism is explicitly designed for emergency use. Any time the admin or protocol owner triggers a pause — a realistic and expected operational event — every call to `addLiquidity()` or `removeLiquidity()` bypasses the guard. No special attacker capability is required; any user or router interacting with the pool in the normal way will bypass the pause.

### Recommendation

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

### Proof of Concept

1. Pool is deployed with a mutable price provider and a `DepositAllowlistExtension` or `OracleValueStopLossExtension`.
2. Oracle begins returning a manipulated price. Protocol owner calls `factory.protocolPausePool(pool)` → `pool.setPause(2)` → `pauseLevel = 2`.
3. Any call to `pool.swap()` now reverts with `PoolPaused`.
4. Attacker (or unsuspecting user via router) calls `pool.addLiquidity(victim, salt, deltas, callbackData, extensionData)` directly. The function has no `whenNotPaused` check; it proceeds through `_beforeAddLiquidity`, `LiquidityLib.addLiquidity`, and `_afterAddLiquidity` without reverting.
5. Victim's tokens are deposited into the compromised pool at the manipulated bin price.
6. When the pool is unpaused after the oracle is corrected, the victim's position is immediately arbitraged at the corrected price, resulting in direct loss of principal. [9](#0-8) [7](#0-6)

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
