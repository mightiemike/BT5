### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of the `addLiquidity` call, i.e., the payer) and instead checks `owner` (the LP share recipient). Any address can call `pool.addLiquidity(owner = allowlisted_address, ...)`, pay the token callback, and have LP shares minted to the allowlisted address — bypassing the intended access control entirely.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the before-hook is dispatched as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

So the extension receives `sender = msg.sender` (the actual depositor/payer) and `owner = owner` (the LP share recipient). [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (first parameter is unnamed/discarded) and checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Because `addLiquidity` imposes **no restriction** on who may specify any `owner` value (unlike `removeLiquidity`, which enforces `msg.sender == owner`), an unauthorized caller B can pass `owner = A` (an allowlisted address). The extension evaluates `allowedDepositor[pool][A]` → `true`, the hook passes, B pays tokens via the swap callback, and A receives LP shares. The depositor identity the admin intended to gate (`sender`) is never checked. [3](#0-2) 

The asymmetry with `SwapAllowlistExtension.beforeSwap` confirms this is the wrong variable: the swap guard correctly checks `sender` (the caller of `swap`), not `recipient`. [4](#0-3) 

The contract's own NatSpec states: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is the address that pays tokens — `sender` — not the LP share recipient `owner`. [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is an admin-configured access control meant to restrict which addresses may add liquidity to the pool. Because the check is on the wrong variable (`owner` instead of `sender`), any unprivileged address can bypass it by specifying an allowlisted `owner`. This breaks the pool admin's intended security boundary: unauthorized parties can inject liquidity into a restricted pool, manipulating bin state and per-share metrics consumed by extensions such as `OracleValueStopLossExtension` in ways the admin explicitly sought to prevent. Unauthorized liquidity addition can shift `totalShares` and bin balances, causing the stop-loss watermark comparisons to diverge from the admin's expected pool composition. [6](#0-5) 

---

### Likelihood Explanation

The bypass requires only a direct call to `pool.addLiquidity` with `owner` set to any allowlisted address. No special privileges, flash loans, or complex setup are needed. Any address can execute this at any time against any pool that has `DepositAllowlistExtension` configured.

---

### Recommendation

Check `sender` (the actual depositor/caller) instead of `owner`:

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

1. Deploy pool with `DepositAllowlistExtension` configured.
2. Admin calls `setAllowedToDeposit(pool, A, true)` — only address A is allowlisted; address B is not.
3. B calls `pool.addLiquidity(owner = A, salt, deltas, callbackData, extensionData)`.
4. Pool dispatches `extension.beforeAddLiquidity(sender=B, owner=A, ...)`.
5. Extension evaluates `allowedDepositor[pool][A]` → `true` → hook passes without checking B.
6. `LiquidityLib.addLiquidity` mints LP shares to A; B pays tokens via the callback.
7. B (unauthorized) has successfully added liquidity to the restricted pool, bypassing the allowlist entirely.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L236-242)
```text
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
```
