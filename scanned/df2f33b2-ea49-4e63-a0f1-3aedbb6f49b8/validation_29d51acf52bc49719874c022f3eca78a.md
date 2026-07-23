### Title
Liquidity Added with `owner = address(0)` Permanently Locks Deposited Tokens Due to Missing Zero-Address Guard — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`addLiquidity` in `MetricOmmPool.sol` accepts an arbitrary `owner` address with no zero-address check. When `owner = address(0)` is supplied, LP shares are minted to `address(0)` and `binTotals` is updated to include the deposited tokens. Because `removeLiquidity` enforces `msg.sender == owner`, and `msg.sender` can never be `address(0)`, the deposited tokens are permanently irrecoverable. The extension hook path (`_beforeAddLiquidity`) is called with `sender = msg.sender` and `owner = address(0)` — any configured `DepositAllowlistExtension` guards the caller identity but cannot prevent the creation of an unremovable position for the zero address, leaving the guard misapplied against this vector.

---

### Finding Description

`addLiquidity` accepts `owner` from the caller without validation:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L182-L196
function addLiquidity(
    address owner,          // ← no address(0) check
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData,
      binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
}
``` [1](#0-0) 

`LiquidityLib.addLiquidity` records shares under the key derived from `owner` and updates `binTotals.scaledToken0/1` to include the deposited amounts. Both state mutations proceed normally with `owner = address(0)`.

The removal path enforces the opposite invariant:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L206
if (msg.sender != owner) revert NotPositionOwner();
``` [2](#0-1) 

Because `msg.sender` is never `address(0)` in EVM execution, no caller can ever satisfy `msg.sender == address(0)`. The position is permanently unremovable.

**Extension hook misapplication:** `_beforeAddLiquidity` forwards `sender` (the actual `msg.sender`) and `owner` (the supplied zero address) to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  L88-L99
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
``` [3](#0-2) 

A `DepositAllowlistExtension` configured on the pool guards `sender` (the caller) against an allowlist. It cannot and does not prevent an allowlisted sender from minting a position to `address(0)` as `owner`. The guard is structurally misbound: it validates the actor performing the deposit but not the beneficiary address that will own — and must later reclaim — the position. This is the direct analog of the external report's `_transferFrom` guard that checks the caller but not `_to`.

---

### Impact Explanation

- **Direct loss of user principal:** Tokens transferred into the pool via the `addLiquidity` callback are credited to `binTotals` but can never be withdrawn. They are permanently locked.
- **Pool accounting corruption:** `binTotals.scaledToken0` and `binTotals.scaledToken1` permanently include the locked amounts. Every subsequent LP share valuation, fee calculation, and solvency check is inflated by these phantom balances, causing incorrect accounting for all remaining LPs.
- **Unburnable shares:** The `_positionBinShares` mapping retains non-zero entries for the `address(0)` position key indefinitely, contributing to `_binTotalShares` and distorting per-share redemption values for legitimate LPs.

---

### Likelihood Explanation

Any unprivileged caller of `addLiquidity` can trigger this by passing `owner = address(0)`. Realistic paths include:

1. A buggy router or aggregator that derives `owner` from user-supplied calldata without sanitization.
2. A user who accidentally passes the zero address (e.g., an uninitialized variable in an integration contract).
3. A malicious actor who deliberately locks a victim's tokens by front-running or social-engineering a router call.

No special role or privileged access is required. The pool imposes no restriction on who may call `addLiquidity` or what `owner` value they supply.

---

### Recommendation

Add a zero-address guard at the top of `addLiquidity` (and defensively in `removeLiquidity`):

```solidity
function addLiquidity(
    address owner,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    require(owner != address(0), InvalidOwner());   // ← add this
    if (deltas.binIdxs.length == 0) return (0, 0);
    ...
}
```

This mirrors the fix recommended in the external report: enforce the invariant at the entry point so that no downstream state mutation can proceed with an invalid beneficiary address.

---

### Proof of Concept

1. Attacker (or buggy router) calls:
   ```solidity
   pool.addLiquidity(
       address(0),   // owner = zero address
       0,            // salt
       deltas,       // non-zero bin shares
       callbackData,
       ""
   );
   ```
2. `_beforeAddLiquidity` fires; any configured `DepositAllowlistExtension` approves because `msg.sender` is allowlisted — `owner` is not checked.
3. `LiquidityLib.addLiquidity` mints shares to the `address(0)` position key and increases `binTotals.scaledToken0/1` by the deposited amounts.
4. The swap callback transfers real tokens from the caller into the pool.
5. Any attempt to call `removeLiquidity(address(0), ...)` reverts with `NotPositionOwner` because `msg.sender != address(0)` is always true.
6. The deposited tokens are permanently locked; `binTotals` is permanently inflated; all subsequent LP redemptions are computed against an overstated pool balance.

### Citations

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
