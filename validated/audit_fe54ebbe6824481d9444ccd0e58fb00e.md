Audit Report

## Title
`DepositAllowlistExtension` checks `owner` (position recipient) instead of `sender` (actual depositor), allowing any unprivileged caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and only checks `allowedDepositor[pool][owner]`, where `owner` is a caller-supplied position recipient. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can pass an allowlisted address as `owner`, satisfy the extension check, and deposit tokens into a permissioned pool. This fully defeats the access gate the pool admin configured.

## Finding Description
`ExtensionCalling._beforeAddLiquidity` correctly encodes both `sender` (`msg.sender` of `addLiquidity`) and `owner` (caller-supplied recipient) and forwards them to every configured extension: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` names the first argument `_` (discarded) and only checks the second argument `owner` against `allowedDepositor[msg.sender][owner]`: [2](#0-1) 

`MetricOmmPool.addLiquidity` accepts any caller-supplied `owner` without verifying the caller is that owner, then passes `msg.sender` as `sender` and the arbitrary `owner` to the hook: [3](#0-2) 

`removeLiquidity`, by contrast, enforces `msg.sender == owner`: [4](#0-3) 

This asymmetry is the crux: the extension checks the wrong identity (`owner` instead of `sender`), and the pool imposes no constraint preventing `owner` from being an arbitrary allowlisted address. The existing guard (`allowedDepositor[pool][owner]`) is therefore trivially satisfied by any caller who supplies an allowlisted address as `owner`.

## Impact Explanation
Any unprivileged address can deposit into a pool protected by `DepositAllowlistExtension` by passing an allowlisted address as `owner`. The attacker's tokens are transferred to the pool and credited to the allowlisted address's position; only that address can call `removeLiquidity` to recover them. Consequences include: (1) complete bypass of the pool admin's permissioned LP set, violating the core access-control invariant; (2) dilution of existing allowlisted LPs' proportional share of accrued spread fees and pool assets; (3) the attacker's deposited funds are permanently locked in the victim's position unless the victim voluntarily withdraws and returns them.

## Likelihood Explanation
The trigger requires no special role — any EOA or contract can call `addLiquidity` directly on the pool. The only information needed is one allowlisted address, which is publicly discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. There is no economic barrier beyond gas and the deposited amount (which is locked, not destroyed). Pools using `DepositAllowlistExtension` for regulatory compliance or curated LP sets are the exact target, and the bypass is trivially executable on-chain.

## Recommendation
Change `DepositAllowlistExtension.beforeAddLiquidity` to check the first parameter (`sender` — the actual `msg.sender` of `addLiquidity`) rather than `owner`:

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

Alternatively, enforce `msg.sender == owner` inside `MetricOmmPool.addLiquidity` so that the `owner` parameter cannot be used as a proxy identity.

## Proof of Concept
```solidity
// Setup: pool has DepositAllowlistExtension; alice is allowlisted, bob is not.
// allowedDepositor[pool][alice] == true
// allowedDepositor[pool][bob]   == false

// Bob executes:
pool.addLiquidity(
    alice,        // owner — allowlisted, passes the extension check
    salt,
    deltas,       // desired liquidity amounts
    callbackData, // Bob's callback transfers Bob's tokens to the pool
    extensionData
);

// Result:
// - DepositAllowlistExtension checks allowedDepositor[pool][alice] → true → no revert
// - Bob's metricOmmSwapCallback fires; Bob pays tokens into the pool
// - Alice's position (keyed by alice+salt) is credited with the new shares
// - Bob has deposited into a permissioned pool without being allowlisted
// - Bob's tokens are locked; only alice can call removeLiquidity
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```
