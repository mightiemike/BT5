### Title
`addLiquidity()` Bypasses `whenNotPaused` Guard While `swap()` Enforces It, Allowing Token Deposits Into Paused Pools — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.swap()` is protected by the `whenNotPaused` modifier, but `addLiquidity()` and `removeLiquidity()` are not. When the pool is paused (e.g., due to a compromised oracle or admin intervention), users can still deposit tokens into the pool via `addLiquidity()`. If the pool is later unpaused while the oracle is still bad, those freshly deposited tokens are immediately exposed to adversarial swaps at manipulated prices, causing direct LP principal loss.

---

### Finding Description

`MetricOmmPool` maintains a `pauseLevel` state variable with three levels:

- `0` = active
- `1` = paused by admin
- `2` = paused by protocol [1](#0-0) 

The `whenNotPaused` modifier enforces this guard: [2](#0-1) [3](#0-2) 

`swap()` correctly applies this modifier: [4](#0-3) 

However, `addLiquidity()` does **not** apply `whenNotPaused`: [5](#0-4) 

Nor does `removeLiquidity()`: [6](#0-5) 

Inside `addLiquidity()`, the pool calls `_beforeAddLiquidity()` (extension hooks), then `LiquidityLib.addLiquidity()` which pulls tokens from the caller via a callback, and then `_afterAddLiquidity()` — all without ever checking `pauseLevel`: [7](#0-6) 

The extension hooks (`_beforeAddLiquidity`, `_afterAddLiquidity`) are optional and pool-specific — they are not a substitute for the core pool's own pause guard. [8](#0-7) 

---

### Impact Explanation

The critical scenario is a **pause-then-unpause oracle attack**:

1. The pool is paused (`pauseLevel = 1` or `2`) because the oracle is stale, inverted, or under manipulation.
2. While paused, `addLiquidity()` succeeds — a user (or an attacker front-running the unpause) deposits tokens into the pool. Swaps are blocked so the deposit appears safe.
3. The admin unpauses the pool (`pauseLevel = 0`).
4. The oracle is still bad (or an attacker controls the oracle timing). Swaps immediately execute at the manipulated price, draining the newly deposited LP tokens.
5. The LP suffers direct loss of principal with no recourse.

Even without oracle manipulation, depositing into a paused pool is contrary to the protocol's stated intent: the pause mechanism exists precisely to halt all fund-impacting operations. Allowing deposits while blocking swaps creates an asymmetric risk: LPs can put tokens in but cannot earn fees (swaps blocked), and their tokens are exposed the moment the pool resumes.

**Severity: Medium** — requires the pool to be paused and then unpaused (semi-trusted admin action), but the trigger for the loss (the `addLiquidity()` call during the pause window) is fully unprivileged and reachable by any user.

---

### Likelihood Explanation

- Pools are paused by admin or protocol for legitimate operational reasons (oracle issues, security incidents).
- The pause window is a known, observable on-chain state — any user or bot can detect it and call `addLiquidity()`.
- No special permissions are required to call `addLiquidity()`.
- The inconsistency is structural: `swap()` has the guard, `addLiquidity()` does not — this is not a configuration issue but a missing modifier.

---

### Recommendation

Apply the `whenNotPaused` modifier to `addLiquidity()` to match the guard already present on `swap()`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
```

`removeLiquidity()` should intentionally remain unguarded (users must always be able to exit), which is consistent with the external report's resolution pattern and the protocol's solvency obligations to existing LPs.

---

### Proof of Concept

```
State: pauseLevel = 1 (pool paused by admin due to oracle anomaly)

1. Alice calls pool.addLiquidity(alice, 0, deltas, callbackData, "")
   → No whenNotPaused check → succeeds
   → LiquidityLib.addLiquidity() pulls 1000 USDC + 1 ETH from Alice via callback
   → Alice now holds shares in the paused pool

2. Admin calls factory.setPause(poolAddr, 0)  // unpauses pool
   → pauseLevel = 0

3. Attacker calls pool.swap(attacker, true, largeAmount, 0, callbackData, "")
   → whenNotPaused passes (pool is now active)
   → Oracle returns manipulated/stale price
   → Attacker drains Alice's newly deposited tokens at bad price

Result: Alice loses principal deposited during the pause window.
``` [9](#0-8) [10](#0-9)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L201-201)
```text
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
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
