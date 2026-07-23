### Title
Missing zero-address check on `owner` in `addLiquidity` permanently locks deposited tokens — (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`MetricOmmPool.addLiquidity` accepts `owner = address(0)` without reverting. Because `removeLiquidity` enforces `msg.sender == owner`, a position minted to `address(0)` can never be withdrawn. Any tokens deposited into that position are permanently locked in the pool.

---

### Finding Description

`addLiquidity` records the LP position under the caller-supplied `owner` key: [1](#0-0) 

No validation is performed on `owner` before the position is written and the callback pulls tokens from the caller. The pool then enforces ownership only at removal time: [2](#0-1) 

Because `msg.sender` can never equal `address(0)`, a position minted to `address(0)` is irremovable. The tokens paid during the `metricOmmModifyLiquidityCallback` are permanently stranded inside the pool.

The periphery helper `MetricOmmPoolLiquidityAdder` does guard against this in its own entry points: [3](#0-2) 

However, the pool itself is a public contract. Any caller that bypasses the periphery — a custom router, an integrating protocol, or a direct EOA call — receives no protection. The pool is the last line of defense and must enforce the invariant itself.

---

### Impact Explanation

Tokens deposited into a zero-address position are permanently locked. The caller pays the full token amounts via the liquidity callback but receives a position that can never be redeemed. This constitutes a direct, irreversible loss of user principal with no recovery path.

---

### Likelihood Explanation

Any direct caller of `MetricOmmPool.addLiquidity` — including integrating contracts, custom routers, or users interacting with the pool without the official periphery — can trigger this by passing `owner = address(0)`. The periphery guard is not enforced at the pool level, so the protection is easily bypassed by routing around `MetricOmmPoolLiquidityAdder`.

---

### Recommendation

Add a zero-address check at the top of `addLiquidity` in `MetricOmmPool.sol`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (owner == address(0)) revert InvalidPositionOwner();
    // ... existing logic
}
```

This mirrors the guard already present in `MetricOmmPoolLiquidityAdder._validateOwner` and ensures the invariant is enforced regardless of which entry path is used.

---

### Proof of Concept

1. Attacker (or misconfigured integrator) calls `pool.addLiquidity(address(0), salt, deltas, callbackData, extensionData)` directly.
2. The pool processes the liquidity delta, writes shares to `_positionBinShares[keccak256(address(0), salt, binIdx)]`, and invokes `metricOmmModifyLiquidityCallback` on `msg.sender`.
3. The callback pulls `amount0Added` and `amount1Added` from the caller — tokens are now inside the pool.
4. Any subsequent call to `pool.removeLiquidity(address(0), salt, deltas, extensionData)` reverts at line 206 (`NotPositionOwner`) because `msg.sender != address(0)` is always true.
5. The deposited tokens are permanently locked; no address can ever satisfy the ownership check. [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
