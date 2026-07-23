### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument and checks `owner` instead. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any address — including one that is explicitly not on the allowlist — can call `addLiquidity(allowlistedAddress, ...)` and pass the gate unconditionally.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two identity parameters: `sender` (the actual `msg.sender` of `addLiquidity`, i.e. the payer) and `owner` (the position owner who receives LP shares). The implementation drops `sender` and checks only `owner`:

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

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`, with no requirement that they match:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
// line 192-194
(amount0Added, amount1Added) = LiquidityLib.addLiquidity(
    _liquidityContext(), owner, salt, deltas, callbackData, ...
);
```

`removeLiquidity` enforces `msg.sender == owner`, but `addLiquidity` does not. The callback to pay tokens is issued to `msg.sender`, so the actual payer is the caller, not `owner`.

Contrast with `SwapAllowlistExtension`, which correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

---

### Impact Explanation

A pool configured with `DepositAllowlistExtension` to restrict LP participation to a curated set of addresses provides no real restriction. Any unprivileged address can:

1. Call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`.
2. The hook checks `allowedDepositor[pool][allowlistedAddress]` — passes.
3. The caller pays tokens via the modify-liquidity callback; LP shares are minted to `allowlistedAddress`.

The pool admin's access-control boundary is fully bypassed by an unprivileged path. The allowlist — the sole mechanism the pool admin has to restrict who can deposit — is rendered inoperative. Any actor can inject liquidity into any bin at any time, regardless of allowlist status, affecting pool liquidity distribution and potentially enabling griefing of LP positions or manipulation of bin composition ahead of swaps.

---

### Likelihood Explanation

Likelihood is high. The bypass requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `addLiquidity` directly on the pool with an arbitrary `owner`. The `MetricOmmPoolLiquidityAdder` periphery contract also exposes `addLiquidityExactShares(pool, owner, ...)` with a caller-supplied `owner`, providing an additional entry point through which the same bypass is reachable.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) rather than `owner`:

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

This mirrors the pattern already used correctly in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

```solidity
// Pool is deployed with DepositAllowlistExtension.
// Admin allowlists `alice` but NOT `attacker`.
extension.setAllowedToDeposit(pool, alice, true);

// Attacker (not allowlisted) calls addLiquidity with owner = alice.
// The hook checks allowedDepositor[pool][alice] == true → passes.
// Attacker pays tokens; alice receives LP shares.
// Attacker has deposited into a restricted pool without being on the allowlist.
vm.prank(attacker);
pool.addLiquidity(
    alice,          // owner — checked by the hook, passes
    salt,
    deltas,
    callbackData,   // attacker implements callback to pay tokens
    extensionData
);
// No revert. Attacker bypassed the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
