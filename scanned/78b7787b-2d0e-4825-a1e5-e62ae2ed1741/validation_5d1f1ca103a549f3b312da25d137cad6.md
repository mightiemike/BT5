### Title
`addLiquidity` and `removeLiquidity` Bypass `whenNotPaused` Guard While `swap` Is Correctly Gated — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool.swap` is protected by the `whenNotPaused` modifier, but `addLiquidity` and `removeLiquidity` carry no such guard. When the pool is paused (e.g., due to a compromised oracle or detected exploit), swaps are correctly blocked, yet LPs can still deposit funds into or withdraw funds from the pool. This is the direct structural analog of the GoGoPool M-20 finding: a guard is applied to one side of a paired operation but silently omitted from the other, breaking the intended safety invariant.

### Finding Description

`MetricOmmPool` exposes a `pauseLevel` state variable and a `whenNotPaused` modifier that reverts when `pauseLevel != 0`. [1](#0-0) 

`swap` correctly applies this modifier: [2](#0-1) 

`addLiquidity` and `removeLiquidity` do not: [3](#0-2) [4](#0-3) 

`simulateSwapAndRevert` also lacks the guard, meaning extension hooks (`_beforeSwap`, `_afterSwap`) with external side-effects are reachable while the pool is paused: [5](#0-4) 

### Impact Explanation

The pause mechanism exists to freeze pool activity during emergencies (oracle compromise, accounting anomaly, detected exploit). Blocking only `swap` while leaving `addLiquidity` open means:

1. **LP fund exposure on unpause**: An LP can deposit into a pool that is paused because its oracle is returning bad prices. When the admin unpauses the pool, the LP's newly deposited funds are immediately exposed to arbitrage at the bad oracle price, causing direct principal loss.

2. **Race-condition drain on `removeLiquidity`**: If the pool is paused due to an accounting bug or insolvency event, `removeLiquidity` remaining open allows a subset of LPs to exit before others, leaving remaining LPs with undercollateralised claims — a pool insolvency scenario.

3. **Extension hook side-effects bypass**: `simulateSwapAndRevert` calls `_beforeSwap` and `_afterSwap` extension hooks while paused. Extensions such as `OracleValueStopLossExtension` or `PriceVelocityGuardExtension` that record observations or update state on each hook invocation can be manipulated while the pool is paused, corrupting guard state before the pool resumes. [6](#0-5) 

### Likelihood Explanation

Any pause event (admin-triggered via `setPause` or protocol-triggered) immediately creates the window. The pool admin is a semi-trusted actor whose pause action is explicitly in scope. Any LP or allowlisted depositor can call `addLiquidity` during the pause without any additional privilege. [7](#0-6) 

### Recommendation

Apply `whenNotPaused` to `addLiquidity` and `simulateSwapAndRevert`. For `removeLiquidity`, the protocol should make an explicit design decision: if emergency exits are intentional, document it; otherwise apply the guard there too.

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function simulateSwapAndRevert(...) external whenNotPaused nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) ...
```

### Proof of Concept

1. Admin calls `factory.setPause(poolAddress, 1)` → `pauseLevel = 1`.
2. Any call to `swap(...)` reverts with `PoolPaused()`. ✓
3. An LP calls `addLiquidity(owner, salt, deltas, callbackData, extensionData)` — **succeeds**, tokens are transferred into the pool via the `LiquidityLib.addLiquidity` callback path.
4. Admin calls `factory.setPause(poolAddress, 0)` to unpause (e.g., believing the oracle is fixed, but it is not).
5. An arbitrageur calls `swap(...)` against the LP's newly deposited liquidity at the stale/bad oracle price, draining the LP's principal. [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L314-360)
```text
  ) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());
    if (bidPriceX64 >= askPriceX64) revert BidGreaterThanAsk();
    if (bidPriceX64 == 0) revert BidIsZero();

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

    (uint256 midPriceX64, uint256 baseFeeX64) = SwapMath.midAndSpreadFeeX64FromBidAsk(bidPriceX64, askPriceX64);

    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );

    _nonReentrantAfter();
    revert SimulateSwap(amount0Delta, amount1Delta);
  }
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
