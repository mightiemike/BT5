Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositor, i.e., `msg.sender` of `addLiquidity`) and gates access on `owner` (the LP-position recipient) instead. Because `owner` is a freely chosen caller-supplied argument with no pool-level restriction, any unprivileged address can bypass the allowlist by naming any already-allowlisted address as `owner`. The deposit allowlist guard is rendered completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` correctly passes `msg.sender` as `sender` and the caller-supplied `owner` as the second argument to `_beforeAddLiquidity`: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity`, however, leaves the first parameter (`sender`) unnamed and checks `owner` instead: [2](#0-1) 

The contract's own NatSpec and the `setAllowedToDeposit` parameter name (`depositor`) both declare the intent is to gate the actual depositor: [3](#0-2) 

`owner` is entirely unconstrained — `addLiquidity` imposes no restriction on who may be named as `owner`. Any caller can pass any allowlisted address as `owner` and the check `allowedDepositor[msg.sender][owner]` will return `true`, bypassing the guard. The actual depositor (`sender`) is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and discards the `recipient`: [4](#0-3) 

## Impact Explanation
The deposit allowlist guard is completely ineffective. Any address — regardless of allowlist status — can deposit into a pool that the admin intended to restrict by naming any allowlisted address as `owner`. This constitutes an admin-boundary break: an unprivileged path bypasses a pool-admin-configured access control guard. Pools relying on this extension for KYC, whitelist, or compliance gating receive no protection. The exact wrong value is the extension decision (`allowedDepositor[msg.sender][owner]` evaluates to `true` for an unauthorized depositor).

## Likelihood Explanation
The bypass requires only a single `addLiquidity` call with any allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. The attacker must supply the tokens (they pay), and LP shares are credited to the named `owner` rather than the attacker, limiting direct financial incentive — but the pool's access-control invariant is broken for every deposit made this way, and the attacker can still influence pool liquidity composition.

## Recommendation
Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is meant to deposit.
3. Attacker (`bob`, not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob` successfully deposits; LP shares are credited to `alice`. The allowlist is bypassed. [2](#0-1) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-19)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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
