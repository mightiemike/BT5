### Title
`addLiquidity` Missing `whenNotPaused` Guard Allows Deposits Into Paused Pool — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool.swap()` is protected by the `whenNotPaused` modifier, but `addLiquidity()` is not. When an admin or protocol pauses the pool (e.g., in response to oracle manipulation or a discovered vulnerability), new LPs can still deposit principal into the compromised pool while swaps — the only exit path — are blocked.

### Finding Description

`MetricOmmPool` exposes a `pauseLevel` state variable with two non-zero levels (1 = admin-paused, 2 = protocol-paused). The `whenNotPaused` modifier enforces this guard: [1](#0-0) [2](#0-1) 

`swap()` correctly applies this modifier: [3](#0-2) 

`addLiquidity()` does not: [4](#0-3) 

The function proceeds through `_beforeAddLiquidity` extension hooks, `LiquidityLib.addLiquidity` (which mints shares and pulls tokens from the caller via callback), and `_afterAddLiquidity` hooks — all without checking `pauseLevel`.

None of the built-in extensions (`DepositAllowlistExtension`, `OracleValueStopLossExtension`) compensate for this gap. `DepositAllowlistExtension` only gates by address allowlist, not by pool pause state: [5](#0-4) 

### Impact Explanation

When a pool is paused due to a security incident (oracle manipulation, price feed failure, discovered exploit), new depositors can still transfer principal into the pool. Because `swap()` is simultaneously blocked by `whenNotPaused`, those depositors have no on-chain exit path through the pool. Their funds remain locked in a pool that was paused precisely because it is unsafe, directly risking loss of user principal.

### Likelihood Explanation

The pause mechanism is an active admin/protocol control with two distinct levels, indicating it is expected to be used in adversarial conditions. Any user who deposits during a pause window — whether through a UI that has not yet reflected the pause, a bot, or a direct contract call — is affected. The trigger requires only that the pool be paused, which is a normal operational event.

### Recommendation

Add the `whenNotPaused` modifier to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

Whether `removeLiquidity` should also be blocked is a design choice — many protocols intentionally allow LP exits during a pause — but `addLiquidity` introducing new principal into a paused pool has no legitimate justification.

### Proof of Concept

1. Admin detects oracle manipulation and calls `factory.setPause(pool, 1)` → `pool.setPause(1)` sets `pauseLevel = 1`.
2. `pool.swap(...)` reverts with `PoolPaused()` — swaps are blocked.
3. Attacker (or uninformed LP) calls `pool.addLiquidity(owner, salt, deltas, callbackData, extensionData)` — no `whenNotPaused` check, call succeeds, tokens are transferred into the pool.
4. LP's funds are now locked in a pool known to be in an unsafe state, with no swap-based exit available until the pool is unpaused. [6](#0-5) [7](#0-6)

### Citations

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
