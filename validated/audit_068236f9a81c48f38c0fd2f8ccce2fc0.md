### Title
Missing `owner` Address Validation in `addLiquidity` Permanently Locks LP Principal — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address with no zero-address check. If a user passes `address(0)` as `owner`, the LP position is recorded under the zero address. Because `removeLiquidity` enforces `msg.sender == owner`, and no EOA can ever be `msg.sender == address(0)`, the deposited tokens are permanently locked with no recovery path.

---

### Finding Description

`addLiquidity` is a permissionless function. The `owner` parameter controls who owns the resulting LP position and is the only address that can later call `removeLiquidity` to reclaim the underlying tokens. [1](#0-0) 

The function passes `owner` directly into `LiquidityLib.addLiquidity` and into the `_beforeAddLiquidity` hook without any `require(owner != address(0))` guard: [2](#0-1) 

`removeLiquidity` enforces `msg.sender == owner` as its sole ownership check: [3](#0-2) 

If `owner == address(0)`, the position is permanently orphaned. There is no admin rescue path, no factory override, and no fallback in `removeLiquidity` for this case.

The extension hook system (`_beforeAddLiquidity`) receives `owner` and could theoretically reject it, but only if the pool was deployed with an extension that performs this check. The core contract itself provides no such guarantee. [4](#0-3) 

---

### Impact Explanation

Any tokens deposited via `addLiquidity(..., owner=address(0), ...)` are irrecoverably locked inside the pool. The pool's `binTotals` and `_positionBinShares` accounting correctly records the deposit, but no address can ever satisfy `msg.sender == address(0)` to call `removeLiquidity`. The tokens remain in the pool forever, inflating LP share accounting without being claimable. This is a direct, permanent loss of user principal.

---

### Likelihood Explanation

The scenario is directly analogous to the BitVM Bridge `_btc_addr` bug: a user making a copy-paste error, a front-end bug, or a programmatic integration mistake passes `address(0)` as the owner. The function is permissionless and callable by any address. No off-chain component can reverse the on-chain state after the transaction is confirmed. The likelihood is low-to-medium for individual users but the impact is total loss of the deposited amount with zero recovery.

---

### Recommendation

Add an explicit zero-address check at the top of `addLiquidity`:

```solidity
require(owner != address(0), InvalidOwner());
```

This mirrors the standard pattern used in ERC-721 and ERC-1155 minting functions and is the minimal fix. Optionally, also validate `owner != address(0)` inside `LiquidityLib.addLiquidity` as a defense-in-depth measure.

---

### Proof of Concept

1. Alice calls `pool.addLiquidity(address(0), salt, deltas, callbackData, extensionData)` and transfers `X` token0 and `Y` token1 into the pool via the swap callback.
2. The pool records the position under `_positionBinShares[keccak256(abi.encode(address(0), salt, binIdx))]`.
3. Alice attempts `pool.removeLiquidity(address(0), salt, deltas, extensionData)` — this reverts immediately at `if (msg.sender != owner) revert NotPositionOwner()` because `msg.sender` (Alice) `!= address(0)`.
4. No other address, including the factory or any admin, can satisfy `msg.sender == address(0)`.
5. `X` token0 and `Y` token1 are permanently locked in the pool. [5](#0-4)

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
