Based on my investigation of the codebase, I can identify a concrete analog to the "killed gauge can be voted for" bug class.

---

### Title
Paused Pool Accepts New Liquidity Deposits — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool.addLiquidity` lacks the `whenNotPaused` modifier that guards `swap`. When a pool is paused for security reasons (e.g., oracle compromise), swaps are correctly blocked, but any user can still deposit principal into the compromised pool. This is a direct analog to the VoterV3 pattern: the "alive" check (`pauseLevel`) is applied to one action (`swap`) but omitted from the related action (`addLiquidity`), allowing users to interact with a "killed" pool in a fund-impacting way.

### Finding Description

`MetricOmmPool` exposes a `pauseLevel` state variable with three values: `0` (active), `1` (paused by admin), `2` (paused by protocol). The `whenNotPaused` modifier enforces `pauseLevel == 0`. [1](#0-0) 

The `swap` function correctly applies this guard: [2](#0-1) 

However, `addLiquidity` carries only `nonReentrant` — no `whenNotPaused`: [3](#0-2) 

The `_beforeAddLiquidity` and `_afterAddLiquidity` extension hooks are still dispatched through `ExtensionCalling._callExtensionsInOrder`, meaning any configured extension (e.g., `DepositAllowlistExtension`) runs, but the pool-level pause gate is never checked. [4](#0-3) 

### Impact Explanation

A pool is paused precisely because its state is unsafe — most likely because the oracle feed is compromised, the price provider is returning bad values, or an exploit is being investigated. During this window:

1. `swap` is blocked — no trades can execute at the bad price.
2. `addLiquidity` is **not** blocked — any user can deposit token0 and token1 into the pool.

When the pool is unpaused (or if the security issue is exploited before unpausing), the newly deposited principal participates in swaps at the compromised price, resulting in direct loss of user funds. The depositing user has no way to know the pool was paused for a security reason versus a routine maintenance pause.

**Invariant broken**: A paused pool should not accept new principal. The `pauseLevel` guard is applied inconsistently — it covers `swap` but not `addLiquidity`.

### Likelihood Explanation

- The trigger is fully unprivileged: any address can call `addLiquidity` on a paused pool.
- The prerequisite (pool being paused) is a normal operational event, not a malicious setup assumption — it is the intended safety mechanism.
- This is directly analogous to the VoterV3 pattern: the privileged "kill" action (`setPause`) is not the bug; the bug is that the unprivileged action (`addLiquidity`) does not respect the killed/paused state.

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

`removeLiquidity` intentionally omits `whenNotPaused` to allow LPs to exit — that asymmetry is correct. Only `addLiquidity` needs the guard added.

### Proof of Concept

```
1. Pool is deployed with a mutable price provider (IMMUTABLE_PRICE_PROVIDER == address(0)).
2. The price provider begins returning a manipulated/stale price.
3. Protocol calls factory → pool.setPause(2) to halt swaps. swap() now reverts.
4. Victim calls pool.addLiquidity(...) depositing 100,000 USDC + equivalent token1.
   → No revert. Tokens are transferred in. binTotals updated.
5. Protocol unpauses (setPause(0)) after believing the issue is resolved.
6. Attacker immediately calls swap() at the still-bad oracle price, draining
   the victim's newly deposited tokens at an unfavorable rate.
7. Victim's 100,000 USDC principal is partially or fully lost.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```
