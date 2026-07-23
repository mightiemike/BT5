Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token-paying caller) and gates access on `owner` (the LP-position recipient) instead. Because `pool.addLiquidity` accepts a freely-chosen `owner` with no requirement that `owner == msg.sender`, any unprivileged caller can pass an allowlisted address as `owner` and bypass the allowlist entirely, even though the actual depositor is not allowlisted.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no constraint that `owner == msg.sender`: [1](#0-0) 

It passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` forwards both correctly to the extension: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (the actual `sender`) as unnamed/discarded, and checks only `owner`: [4](#0-3) 

The `_validateOwner` guard in the periphery router only rejects `address(0)` — it does not enforce `owner == msg.sender`: [5](#0-4) 

The NatSpec on `addLiquidity` even explicitly documents the operator pattern (`msg.sender pays but need not equal owner`), confirming there is no protocol-level constraint preventing the separation: [6](#0-5) 

The token callback is invoked on `msg.sender` (the attacker), not on `owner`: [7](#0-6) 

**Exploit path:**
1. Pool admin configures `DepositAllowlistExtension` on a pool, allowlisting `victim` but not `attacker`.
2. Attacker calls `pool.addLiquidity(owner=victim, ...)` directly.
3. The hook receives `sender=attacker` (discarded) and `owner=victim` (checked). `allowedDepositor[pool][victim] == true` → check passes.
4. The callback fires on `msg.sender` (attacker), pulling attacker's tokens into the pool.
5. LP shares are minted to `victim`'s position key. The allowlist is fully bypassed.

## Impact Explanation

This is a complete admin-boundary break: the deposit allowlist — the sole curation mechanism of `DepositAllowlistExtension` — is bypassed by any unprivileged caller with zero privileged access. Any pool relying on this extension for KYC, compliance, or curation has that guarantee nullified. Secondary effects include the attacker irrecoverably losing their tokens (the LP position is under `victim`; `removeLiquidity` enforces `msg.sender == owner`) and the victim receiving unsolicited LP shares. The primary impact is the complete defeat of the pool admin's configured access control, constituting a critical admin-boundary break per the allowed impact gate.

## Likelihood Explanation

The attack requires only a public `pool.addLiquidity` call with any known allowlisted address as `owner`. No privileged role, no special token, no oracle manipulation, no flash loan. Any address can execute it at any time. The only cost is the attacker's own tokens (which are irrecoverably transferred to the victim's position). The pool can be called directly, bypassing the periphery router entirely.

## Recommendation

Check `sender` (the actual depositor/token-provider), not `owner` (the LP-position recipient):

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

## Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypass() public {
    // Setup: victim is allowlisted, attacker is not
    vm.prank(poolAdmin);
    extension.setAllowedToDeposit(address(pool), victim, true);
    // allowedDepositor[pool][attacker] == false

    // Attacker calls pool.addLiquidity directly with owner=victim
    // Attacker implements IMetricOmmModifyLiquidityCallback to pay tokens
    vm.prank(attacker);
    pool.addLiquidity(victim, salt, deltas, callbackData, "");

    // Assert: deposit succeeded despite attacker not being allowlisted
    // beforeAddLiquidity received sender=attacker (discarded), owner=victim (checked)
    // allowedDepositor[pool][victim] == true → passes
    uint256 victimShares = pool.positionBinShares(victim, salt, bin);
    assertGt(victimShares, 0); // passes — allowlist bypassed
}
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```
