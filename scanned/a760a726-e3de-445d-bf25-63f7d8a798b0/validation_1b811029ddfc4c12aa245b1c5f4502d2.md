### Title
Missing Zero-Address Check on `owner` in `addLiquidity` Permanently Locks LP Funds - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and records position shares under that key without validating it is non-zero. Because `removeLiquidity` enforces `msg.sender == owner`, and `msg.sender` can never be `address(0)`, any liquidity deposited with `owner = address(0)` is permanently irrecoverable.

### Finding Description

`addLiquidity` takes a caller-supplied `owner` parameter and passes it directly into `LiquidityLib.addLiquidity`, which stores the minted shares in `_positionBinShares` keyed by `(owner, salt, binIdx)`. [1](#0-0) 

No zero-address guard exists on `owner` before the shares are written: [2](#0-1) 

`removeLiquidity` then enforces:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [3](#0-2) 

Because the EVM never sets `msg.sender` to `address(0)`, a position minted under `owner = address(0)` can never satisfy this check. The tokens transferred in via the `IMetricOmmSwapCallback` during `addLiquidity` are absorbed into `binTotals` and credited to an unreachable position — they are permanently locked.

The factory's `createPool` and `setPoolAdminFeeDestination` both guard their respective address parameters against zero: [4](#0-3) [5](#0-4) 

No equivalent guard exists in `addLiquidity`.

### Impact Explanation

Any tokens deposited into a position with `owner = address(0)` are permanently locked inside the pool. The LP receives no shares they can ever redeem. `binTotals` is updated to include the deposited amounts, so the pool's accounting remains consistent — but the LP's principal is unrecoverable. This is a direct loss of user principal above Sherlock thresholds.

### Likelihood Explanation

The pool is a low-level contract intended to be called by routers and integrators (e.g., `MetricOmmPoolLiquidityAdder`, `MetricOmmSimpleRouter`). Any router that fails to validate its `owner` argument — or that passes a computed/default address that resolves to zero — will silently lock user funds. The pool itself provides no safety net. The likelihood is medium: it requires an integration error or a direct call with a zero owner, but the consequence is irreversible.

### Recommendation

Add a zero-address check at the top of `addLiquidity` in `MetricOmmPool.sol`:

```solidity
if (owner == address(0)) revert InvalidOwner();
```

This mirrors the pattern already applied to `admin` and `adminFeeDestination` in `_validatePoolParameters`. [1](#0-0) 

### Proof of Concept

1. Attacker (or buggy router) calls `pool.addLiquidity(address(0), salt, deltas, callbackData, extensionData)` with a non-trivial `deltas` specifying shares in one or more bins.
2. The callback fires; the caller transfers `amount0Added` and `amount1Added` tokens to the pool.
3. `binTotals.scaledToken0` / `scaledToken1` are incremented; shares are stored under key `keccak256(abi.encode(address(0), salt, binIdx))`.
4. Any subsequent call to `pool.removeLiquidity(address(0), salt, deltas, extensionData)` reverts with `NotPositionOwner` because `msg.sender != address(0)` is always true.
5. The deposited tokens remain in the pool forever, credited to an unreachable position.

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L438-446)
```text
  function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L548-554)
```text
  function _validatePoolParameters(PoolParameters calldata params) internal view {
    if (params.token0 == address(0) || params.token1 == address(0) || params.token0 == params.token1) {
      revert InvalidTokenConfig();
    }
    if (params.admin == address(0)) revert InvalidAdmin();
    _validatePriceProvider(params.token0, params.token1, params.priceProvider);
    if (params.adminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
```
