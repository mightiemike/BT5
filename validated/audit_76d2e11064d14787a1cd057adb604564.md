Audit Report

## Title
`addLiquidity` Missing `whenNotPaused` Guard Allows Deposits Into Paused Pool — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool.addLiquidity()` lacks the `whenNotPaused` modifier that `swap()` correctly applies, allowing new LPs to deposit principal into a pool that has been paused due to a security incident. Because `swap()` is simultaneously blocked by `whenNotPaused`, depositors have no on-chain exit path through the pool, leaving their funds locked in a pool known to be in an unsafe state.

## Finding Description
The `whenNotPaused` modifier at L174–177 calls `_checkNotPaused()` (L643–644), which reverts with `PoolPaused()` when `pauseLevel != 0`. `swap()` (L217–224) correctly applies this modifier. `addLiquidity()` (L182–196) applies only `nonReentrant(PoolActions.ADD_LIQUIDITY)` — `whenNotPaused` is absent. The function proceeds through `_beforeAddLiquidity`, `LiquidityLib.addLiquidity` (which mints shares and pulls tokens via callback), and `_afterAddLiquidity` with no pause check at any stage. `DepositAllowlistExtension.beforeAddLiquidity()` (L32–42) checks only the depositor address allowlist (`allowedDepositor` / `allowAllDepositors`) and does not inspect `pauseLevel`, so it provides no compensating control. `setPause()` (L455–461) accepts levels 1 and 2, both of which trigger `_checkNotPaused()` to revert — confirming the pause mechanism is intended to block pool activity.

Exploit path:
1. Admin calls `factory.setPause(pool, 1)` → `pauseLevel = 1`.
2. `pool.swap(...)` reverts with `PoolPaused()`.
3. Any caller invokes `pool.addLiquidity(owner, salt, deltas, callbackData, extensionData)` — succeeds, tokens transferred into pool.
4. LP shares are minted; depositor's tokens are locked with no swap-based exit until the pool is unpaused.

## Impact Explanation
Direct loss of user principal: depositors transfer tokens into a pool that was paused precisely because it is unsafe (oracle manipulation, discovered exploit, price feed failure). With `swap()` blocked, there is no on-chain exit path through the pool. This meets the "broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" impact criterion.

## Likelihood Explanation
The pause mechanism has two active levels and is designed for adversarial conditions. Any user whose UI has not yet reflected the pause, any bot, or any direct contract caller can trigger this. No special privilege is required — `addLiquidity` is a public function. The condition (pool paused) is a normal operational event.

## Recommendation
Add `whenNotPaused` to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

## Proof of Concept
```solidity
// 1. Admin pauses the pool
factory.setPause(address(pool), 1); // pauseLevel = 1

// 2. Swap is blocked
vm.expectRevert(PoolPaused.selector);
pool.swap(recipient, true, 1e18, priceLimitX64, "", "");

// 3. addLiquidity succeeds — no whenNotPaused check
LiquidityDelta memory deltas = ...; // valid delta
pool.addLiquidity(owner, salt, deltas, callbackData, ""); // succeeds, tokens transferred

// 4. LP's tokens are now locked; swap still reverts until unpaused
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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
