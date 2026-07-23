### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and validates `owner` (the LP position recipient) instead. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address, any unprivileged caller can bypass the deposit allowlist entirely by setting `owner` to any already-allowlisted address.

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct actor addresses:

- `msg.sender` — the actual caller who pays tokens via the `metricOmmModifyLiquidityCallback` callback
- `owner` — an arbitrary address that receives the LP position (shares credited in `_positionBinShares`)

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both to the extension. The `IMetricOmmExtensions.beforeAddLiquidity` interface exposes both as `sender` and `owner`.

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely (the parameter is unnamed) and only validates `owner`:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The actual token payment is collected from `msg.sender` (the caller), not from `owner`:

```solidity
// LiquidityLib.sol line 147-148
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

The LP shares are credited to `owner`:

```solidity
// LiquidityLib.sol line 121
positionBinShares[posKey] = newUserShares;  // posKey = keccak256(owner, salt, bin)
```

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the first parameter):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The asymmetry is the root cause: the swap guard checks the right actor; the deposit guard does not.

### Impact Explanation

The deposit allowlist provides zero protection against unauthorized deposits. Any unprivileged address can:

1. Call `pool.addLiquidity(owner = allowlistedAddress, ...)` with any allowlisted address as `owner`.
2. The extension checks `allowedDepositor[pool][allowlistedAddress]` → `true` → passes.
3. The caller (non-allowlisted) pays tokens via callback and the allowlisted address receives LP shares.
4. The allowlisted address can then call `removeLiquidity` (which enforces `msg.sender == owner`) and recover the tokens.

The pool admin's access control is completely defeated. The pool admin configured the allowlist to restrict which addresses can deposit (e.g., for KYC/AML compliance, to prevent manipulation, or to control pool composition), but any address can route a deposit through any allowlisted `owner` and inject liquidity into the pool. This is a direct admin-boundary break: an unprivileged path bypasses a pool-admin-configured guard.

### Likelihood Explanation

Likelihood is high. The bypass requires no special permissions, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The allowlisted addresses are discoverable on-chain via the `AllowedToDepositSet` event or the `allowedDepositor` mapping. The attack is a single transaction.

### Recommendation

Check `sender` (the actual token payer) instead of `owner` (the LP position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

### Proof of Concept

```
Setup:
  - Pool P has DepositAllowlistExtension configured.
  - allowedDepositor[P][Alice] = true
  - Bob is NOT allowlisted.

Attack:
  1. Bob deploys a contract that implements IMetricOmmModifyLiquidityCallback,
     transferring the required tokens to the pool in the callback.
  2. Bob calls pool.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...).
  3. Pool calls _beforeAddLiquidity(sender=Bob, owner=Alice, ...).
  4. DepositAllowlistExtension checks allowedDepositor[P][Alice] → true → no revert.
  5. LiquidityLib credits LP shares to Alice (positionBinShares[keccak256(Alice,0,bin)] += shares).
  6. Callback fires to Bob's contract; Bob's contract transfers tokens to the pool.
  7. Alice now holds LP shares she did not request; Bob's tokens are in the pool.
  8. Alice calls removeLiquidity(owner=Alice, ...) and recovers the tokens.

Result: Bob deposited into a restricted pool without being allowlisted.
        The deposit allowlist is completely bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L119-121)
```text
          }
          binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```
