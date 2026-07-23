### Title
`addLiquidity` Bypasses Pause Guard, Allowing Token Deposits Into Paused Pools - (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`MetricOmmPool.addLiquidity` lacks the `whenNotPaused` modifier. When an admin or protocol pauses a pool due to a security incident (oracle compromise, price manipulation, adaptor failure), users can still deposit tokens into the paused pool. Those tokens are then at risk when the pool is unpaused into a still-compromised state.

---

### Finding Description

`MetricOmmPool` maintains a `pauseLevel` state variable and a `whenNotPaused` modifier:

```solidity
uint8 internal pauseLevel;
// 0 = active, 1 = paused by admin, 2 = paused by protocol

modifier whenNotPaused() {
    _checkNotPaused();
    _;
}

function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
}
``` [1](#0-0) [2](#0-1) [3](#0-2) 

The `swap` function correctly applies this guard:

```solidity
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
``` [4](#0-3) 

However, `addLiquidity` does **not** apply `whenNotPaused`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
``` [5](#0-4) 

The pool is oracle-anchored: its pricing is derived entirely from an external price provider. The documented pause rationale covers exactly the scenarios where oracle integrity is in question. When `pauseLevel != 0`, swaps are blocked to prevent bad-price execution, but `addLiquidity` remains open, allowing users to deposit fresh principal into a pool whose price feed is known to be compromised or whose adaptor has a security issue.

The `_beforeAddLiquidity` extension hook fires, but extensions such as `SwapAllowlistExtension` or `DepositAllowlistExtension` guard *who* may deposit, not *whether the pool is safe to deposit into*. No extension substitutes for the missing pause check. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A pool is paused precisely because it is unsafe — oracle compromise, price manipulation, or adaptor vulnerability. Users who call `addLiquidity` during the pause deposit real tokens (token0 and token1) into the pool. When the pool is unpaused (even partially resolved), those tokens are immediately exposed to the same attack vector that triggered the pause. Because the pool is oracle-anchored with no internal price discovery, a compromised or stale oracle can allow a swap that drains the newly deposited liquidity at an incorrect price. The depositing user suffers direct loss of principal with no recourse.

**Severity: Medium** — requires an admin pause event and a user depositing during the pause window, but the consequence is direct loss of user principal, which is within the allowed impact gate.

---

### Likelihood Explanation

- Pool pauses are an expected operational event (the protocol explicitly defines two pause levels).
- Users monitoring a pool may not observe the `PauseLevelUpdated` event before submitting a `addLiquidity` transaction, especially in high-latency or mempool-congested conditions.
- The trigger (calling `addLiquidity` on a paused pool) requires no special privilege — any user or router can do it.

**Likelihood: Low-Medium** — pause events are infrequent but the bypass is unconditional once a pause is active.

---

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

`removeLiquidity` intentionally omits `whenNotPaused` so existing LPs can exit a paused pool — that asymmetry is correct. Only `addLiquidity` needs the guard added.

---

### Proof of Concept

1. Pool is deployed with an oracle-anchored `PriceProvider`. Normal operation proceeds.
2. The oracle feed is compromised (e.g., stale price, inverted bid/ask, or adaptor exploit). The factory admin calls `setPause(1)` on the pool, setting `pauseLevel = 1`.
3. A user (or a router acting on behalf of a user) submits `addLiquidity(owner, salt, deltas, callbackData, extensionData)`. The call succeeds — `nonReentrant` passes, no `whenNotPaused` check exists, `_beforeAddLiquidity` extension hooks pass (they check allowlist membership, not pause state), and `LiquidityLib.addLiquidity` executes, pulling `amount0Added` and `amount1Added` of real tokens from the user into the pool.
4. The admin resolves the incident and calls `setPause(0)`, unpausing the pool.
5. If the oracle issue persists or recurs, an attacker immediately calls `swap` with a manipulated price limit, draining the pool at an incorrect price — including the tokens deposited in step 3.
6. The user who added liquidity in step 3 loses their deposited principal. [8](#0-7) [5](#0-4)

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
