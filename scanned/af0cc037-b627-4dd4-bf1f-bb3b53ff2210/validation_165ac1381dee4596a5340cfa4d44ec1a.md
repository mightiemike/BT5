### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and enforces the allowlist check against `owner` (the position-owner argument). Because `addLiquidity` imposes no `msg.sender == owner` constraint, any address can call `addLiquidity(allowlisted_address, ...)` and pass the guard, depositing tokens into the pool while the allowlist is completely bypassed.

### Finding Description

`DepositAllowlistExtension` is documented as *"Gates `addLiquidity` by depositor address, per pool."* Its `beforeAddLiquidity` hook receives two identity parameters: `sender` (the actual `msg.sender` of `addLiquidity`) and `owner` (the position-owner argument). The implementation discards `sender` and checks `owner`: [1](#0-0) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner`: [2](#0-1) 

`addLiquidity` has no `msg.sender == owner` requirement, so any address may supply an arbitrary `owner`. Tokens are pulled from `msg.sender` (the real depositor) via the swap callback, and shares are credited to `owner`.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the actual caller): [3](#0-2) 

The inconsistency is the root cause: the deposit guard checks the wrong identity.

### Impact Explanation

A pool admin who deploys `DepositAllowlistExtension` to restrict deposits to a curated set of addresses (KYC, whitelist-only LP programs, regulatory compliance) receives no protection. Any unprivileged address can:

1. Pick any allowlisted address `A`.
2. Call `pool.addLiquidity(A, salt, deltas, callbackData, "")`.
3. The extension checks `allowedDepositor[pool][A]` → passes.
4. The caller's tokens are pulled via callback; shares are minted to `A`.

The configured guard is silently defeated. Because `removeLiquidity` enforces `msg.sender == owner`: [4](#0-3) 

the unauthorized depositor permanently loses their tokens to `A`'s position (a self-inflicted loss), but the allowlist invariant — that only approved addresses may participate — is broken for every pool using this extension.

### Likelihood Explanation

The bypass requires only a standard `addLiquidity` call with a known allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Any actor who wants to deposit into a restricted pool can do so trivially.

### Recommendation

Change the allowlist check to validate `sender` (the actual depositor), consistent with `SwapAllowlistExtension`:

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

```solidity
// Setup: pool with DepositAllowlistExtension; only `alice` is allowlisted.
extension.setAllowedToDeposit(address(pool), alice, true);
extension.setAllowedToDeposit(address(pool), bob,  false);

// Bob (not allowlisted) calls addLiquidity with owner = alice.
vm.prank(bob);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// Bob supplies alice as owner — extension checks allowedDepositor[pool][alice] → true → passes.
pool.addLiquidity(alice, someSalt, deltas, callbackData, "");

// Result: bob's tokens are in the pool; alice holds the shares.
// The deposit allowlist provided zero protection against bob.
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L204-212)
```text
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
