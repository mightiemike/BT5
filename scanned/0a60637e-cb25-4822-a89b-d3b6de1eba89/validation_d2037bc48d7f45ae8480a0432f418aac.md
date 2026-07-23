### Title
`addLiquidity` and `removeLiquidity` Missing `whenNotPaused` Guard Allows Fund Exposure During Emergency Pause — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.addLiquidity` and `MetricOmmPool.removeLiquidity` are missing the `whenNotPaused` modifier that is correctly applied to `swap`. When the pool is paused in response to a critical event (exploit, active bug, upgrade), users can still deposit new principal into the compromised pool and extension hooks (`_beforeAddLiquidity`, `_afterAddLiquidity`, `_beforeRemoveLiquidity`, `_afterRemoveLiquidity`) continue to execute, defeating the purpose of the pause mechanism.

---

### Finding Description

`MetricOmmPool` exposes a `pauseLevel` state variable (0 = active, 1 = admin-paused, 2 = protocol-paused) and a `whenNotPaused` modifier that reverts when `pauseLevel != 0`. [1](#0-0) 

The `swap` function correctly applies this guard: [2](#0-1) 

However, `addLiquidity` and `removeLiquidity` do not: [3](#0-2) [4](#0-3) 

Both functions call extension hooks before and after their core logic: [5](#0-4) [6](#0-5) 

These hooks dispatch into configured extension contracts (`EXTENSION_1`–`EXTENSION_7`) via `_callExtensionsInOrder`: [7](#0-6) 

The pool comment explicitly states pausing is reserved for critical scenarios: [8](#0-7) 

---

### Impact Explanation

**Direct loss of user principal.** When the pool is paused because an exploit or active bug is discovered:

1. `swap` is correctly blocked — the attacker cannot drain via the swap path.
2. But `addLiquidity` remains open — unsuspecting LPs (or a front-running bot) can still deposit token0/token1 into the compromised pool.
3. The newly deposited funds are immediately tracked in `binTotals` and `_binStates`, making them subject to whatever invariant is broken.
4. Extension hooks (`_beforeAddLiquidity`, `_afterAddLiquidity`) still execute against potentially vulnerable extension logic, widening the attack surface even while paused.
5. When the pool is eventually unpaused, the newly deposited principal is at full risk.

Additionally, `removeLiquidity` executing while paused means an attacker with a compromised position (e.g., via a flash-loan or reentrancy that was the reason for the pause) can still exit with inflated shares before the admin can react.

---

### Likelihood Explanation

- **Trigger condition**: Pool must be paused — this is an infrequent but explicitly designed scenario.
- **Attacker action**: No special privilege required. Any address can call `addLiquidity`; any position owner can call `removeLiquidity`. Both are standard user operations.
- **Realistic scenario**: A MEV bot or informed attacker monitors the mempool for the `setPause` transaction, front-runs it with `addLiquidity` to seed a position, then back-runs the unpausing to exploit the still-broken invariant. Alternatively, ordinary users unaware of the pause continue to add liquidity.

Likelihood: **Medium** (requires an active pause event, but no privilege escalation).
Impact: **High** (direct loss of deposited principal).
Overall: **High/Medium**.

---

### Recommendation

Apply `whenNotPaused` to both functions, mirroring the pattern already used on `swap`:

```solidity
- function addLiquidity(
-   address owner, uint80 salt, LiquidityDelta calldata deltas,
-   bytes calldata callbackData, bytes calldata extensionData
- ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
+ function addLiquidity(
+   address owner, uint80 salt, LiquidityDelta calldata deltas,
+   bytes calldata callbackData, bytes calldata extensionData
+ ) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {

- function removeLiquidity(
-   address owner, uint80 salt, LiquidityDelta calldata deltas,
-   bytes calldata extensionData
- ) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) {
+ function removeLiquidity(
+   address owner, uint80 salt, LiquidityDelta calldata deltas,
+   bytes calldata extensionData
+ ) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) {
```

If the protocol intentionally allows `removeLiquidity` during a pause (to let LPs exit), this should be explicitly documented and the extension hooks should be skipped during a paused state to prevent hook-level exploitation.

---

### Proof of Concept

```
1. Pool is deployed with a DepositAllowlistExtension or OracleValueStopLossExtension.
2. An exploit is discovered; admin calls factory.setPause(poolAddress, 1).
3. swap() now reverts with PoolPaused — attacker cannot drain via swap.
4. Attacker (or innocent LP) calls pool.addLiquidity(...) — succeeds because
   whenNotPaused is absent.
   - _beforeAddLiquidity fires extension hooks on the still-live extension.
   - LiquidityLib.addLiquidity credits shares and updates binTotals.
   - _afterAddLiquidity fires extension hooks again.
5. Deposited token0/token1 are now tracked in binTotals.scaledToken0/scaledToken1.
6. When the pool is unpaused, the exploit can be triggered against the enlarged pool,
   draining the newly deposited principal.
```

The root cause is the asymmetric application of `whenNotPaused`: present on `swap` (line 224) but absent on `addLiquidity` (line 188) and `removeLiquidity` (line 199). [9](#0-8)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-212)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }

  /// @inheritdoc IMetricOmmPoolActions
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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```
