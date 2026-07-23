### Title
Missing `owner != address(0)` Validation in `addLiquidity` Permanently Locks Deposited Funds - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary

`MetricOmmPool.addLiquidity` accepts `owner = address(0)` without any validation. Because `removeLiquidity` enforces `msg.sender == owner` and `msg.sender` can never be `address(0)`, any position minted to `address(0)` is permanently irremovable. The caller's deposited tokens are locked in the pool forever.

### Finding Description

`addLiquidity` takes a caller-supplied `owner` address that is written directly into the position key (`_positionBinShares`) and used as the sole authorization check in `removeLiquidity`:

```solidity
// MetricOmmPool.sol – addLiquidity (no owner validation)
function addLiquidity(
    address owner,          // ← never checked against address(0)
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
``` [1](#0-0) 

The only ownership gate in the entire liquidity-removal path is:

```solidity
// MetricOmmPool.sol – removeLiquidity
if (msg.sender != owner) revert NotPositionOwner();
``` [2](#0-1) 

Because the EVM guarantees `msg.sender != address(0)` for every external call, a position whose `owner` is `address(0)` can never satisfy this check. The position — and the underlying token balances it represents — is permanently frozen inside the pool.

The token transfer happens via the swap-callback mechanism: `msg.sender` (the actual depositor) supplies the tokens, while `owner` receives the position. If `owner = address(0)`, the depositor's tokens enter `binTotals` and `_binStates` but the corresponding shares are irrecoverable.

### Impact Explanation

- **Direct loss of user principal**: every token deposited into a `owner = address(0)` position is permanently locked; the depositor cannot recover them.
- **Pool accounting distortion**: `binTotals.scaledToken0 / scaledToken1` grow by the locked amount, inflating the apparent liquidity and skewing the bin-position cursor (`curPosInBin`) for all subsequent swaps and LP operations on the pool.
- **Broken core pool functionality**: the remove-liquidity flow is permanently broken for the affected shares, violating the invariant that every LP can exit their position.

### Likelihood Explanation

Any caller of `addLiquidity` — including a router, a multicall aggregator, or a user who zero-initialises a struct — can trigger this by passing `owner = address(0)`. No privileged role is required. A malicious actor can deliberately grief a pool by locking a meaningful amount of liquidity, distorting pricing for all other LPs.

### Recommendation

Add an explicit zero-address guard at the top of `addLiquidity`, mirroring the fix recommended in the referenced Witch.sol report:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    require(owner != address(0), "invalid owner");   // ← add this
    ...
}
```

### Proof of Concept

1. Deploy a `MetricOmmPool` with no extensions (or extensions that do not validate `owner`).
2. Call `pool.addLiquidity(address(0), salt, deltas, callbackData, "")` from any EOA or contract that implements the swap callback.
3. The callback transfers `amount0Added` / `amount1Added` tokens from the caller into the pool; `_positionBinShares[keccak256(address(0), salt, binIdx)]` is incremented.
4. Attempt `pool.removeLiquidity(address(0), salt, deltas, "")` from any address → reverts with `NotPositionOwner` because `msg.sender != address(0)`.
5. The deposited tokens are permanently locked; `binTotals` reflects the inflated balance, distorting all future swap prices for the pool. [1](#0-0) [2](#0-1)

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
