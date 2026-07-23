### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller) and instead checks the `owner` parameter (the position owner). Any address can bypass the allowlist by calling `addLiquidity` with `owner` set to any address that is on the allowlist.

---

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and ignored. The guard only checks whether `owner` is in `allowedDepositor[pool]`: [1](#0-0) 

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

In `MetricOmmPool.addLiquidity`, the pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`: [2](#0-1) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

An unauthorized address (not in `allowedDepositor[pool]`) can call `addLiquidity(allowedAddress, salt, deltas, ...)`. The extension sees `owner = allowedAddress`, which is on the allowlist, and passes. The unauthorized address then pays the tokens via the swap callback, and the position is recorded under `allowedAddress`.

The bug is confirmed by direct comparison with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller) and ignores `recipient`: [3](#0-2) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The asymmetry — `SwapAllowlistExtension` checks `sender`, `DepositAllowlistExtension` checks `owner` — confirms this is a bug, not an intentional design choice. The admin-facing setter also uses the name `depositor`, not `owner`: [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict which addresses may add liquidity to a pool. With this bug the guard is entirely ineffective: any address can deposit into a restricted pool by nominating any allowlisted address as `owner`. The pool admin's access-control boundary is bypassed by an unprivileged path.

Concrete consequences:
- Unauthorized addresses can inject liquidity into restricted pools, shifting bin cursor positions and altering the pool's internal price state.
- Positions are created under allowlisted addresses without their consent, which can be used to grief or manipulate LP accounting.
- If the pool relies on the allowlist to enforce KYC/compliance or to limit LP participation to trusted parties, that invariant is fully broken.

This satisfies the **Admin-boundary break** impact gate: a factory/pool-admin-configured access control is bypassed by an unprivileged path.

---

### Likelihood Explanation

Exploitation requires no special privileges. Any address can call `addLiquidity` on a pool with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`, passing any allowlisted address as `owner`. The attacker must supply the token amounts via the callback (a cost), but there is no other barrier. The allowlisted address to use as `owner` is publicly readable from `allowedDepositor`.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, consistent with how `SwapAllowlistExtension` checks `sender` in `beforeSwap`:

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

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured in `beforeAddLiquidityOrder`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** on the allowlist.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` = `true` → **no revert**.
6. `LiquidityLib.addLiquidity` records the position under `alice`.
7. Pool calls `IMetricOmmSwapCallback(bob).metricOmmSwapCallback(...)` — Bob pays the tokens.
8. Bob has successfully deposited into a restricted pool, bypassing the allowlist entirely. [1](#0-0) [5](#0-4) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
```

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
