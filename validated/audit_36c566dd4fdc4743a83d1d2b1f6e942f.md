Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` validates `owner` instead of `sender`, allowing any unprivileged address to deposit into a restricted pool — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual on-chain caller who pays tokens via callback) and only validates `owner` (the LP-position recipient). Because `MetricOmmPool.addLiquidity` allows any caller to freely specify any `owner` address, an address not on the allowlist can deposit tokens by nominating an allowlisted address as `owner`, bypassing the guard entirely.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument and the caller-supplied `owner` as the second argument to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The actual token payment is collected from `msg.sender` (the caller) via `metricOmmModifyLiquidityCallback` inside `LiquidityLib.addLiquidity`. The `owner` parameter only determines who holds the resulting LP position.

`DepositAllowlistExtension.beforeAddLiquidity` receives both addresses but discards the first (`sender`) by leaving it unnamed, and exclusively checks `owner` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Here `msg.sender` is the pool address (the extension is called by the pool), so the check is `allowedDepositor[pool][owner]`. An unauthorized address Bob can call `addLiquidity(owner=Alice, ...)` where Alice is allowlisted. The extension checks `allowedDepositor[pool][Alice]` → true → passes. Bob's callback pays the tokens; Alice receives the LP position. The allowlist is fully bypassed.

`removeLiquidity` enforces `if (msg.sender != owner) revert NotPositionOwner()` (L206), so only Alice can withdraw — but with collusion, Alice can return proceeds to Bob off-chain, completing a full end-to-end bypass.

## Impact Explanation
This is an admin-boundary break: a pool-admin-configured access-control restriction is bypassed by an unprivileged path. Concrete impacts include:

1. **Compliance violation**: A sanctioned or KYC-rejected address deposits funds into a restricted pool by nominating any allowlisted address as `owner`. The pool receives liquidity from an unauthorized source with no on-chain record tying it to the actual payer.
2. **Allowlist rendered ineffective**: Any pool relying on `DepositAllowlistExtension` to enforce a closed LP set (institutional pools, permissioned vaults) can be infiltrated by any external party.
3. **Full bypass with collusion**: Bob deposits via `owner=Alice`, Alice calls `removeLiquidity` and returns proceeds to Bob off-chain. The allowlist is circumvented end-to-end.

The deposit bypass (Step 2) is unconditional and requires no special privilege — any EOA or contract can execute it.

## Likelihood Explanation
- No special privilege is required; `addLiquidity` is a public function with a freely chosen `owner` parameter.
- Allowlisted depositor addresses are discoverable on-chain via `AllowedToDepositSet` events.
- The deposit bypass itself (Step 2) is unconditional. Full fund recovery requires a cooperating allowlisted address, introducing friction but not preventing the compliance/access-control violation.

Likelihood: **Medium** (deposit bypass is unconditional; full fund recovery requires collusion).

## Recommendation
Check `sender` (the actual payer) instead of, or in addition to, `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    address pool_ = msg.sender;
    if (!allowAllDepositors[pool_]
        && !allowedDepositor[pool_][sender]
        && !allowedDepositor[pool_][owner])
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate by LP-position holder only, document this explicitly and rename the mapping/event to `allowedOwner` to avoid confusion. If the intent is to gate by depositor (payer), `sender` must be checked.

## Proof of Concept
```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Alice] = true
  allowedDepositor[pool][Bob]   = false   // Bob is NOT authorized

Step 1 (expected revert):
  Bob calls pool.addLiquidity(owner=Bob, ...)
  → extension checks allowedDepositor[pool][Bob] → false → reverts ✓

Step 2 (bypass — unconditional):
  Bob calls pool.addLiquidity(owner=Alice, ...)
  → _beforeAddLiquidity(Bob, Alice, ...) is called
  → extension discards Bob (unnamed first param), checks allowedDepositor[pool][Alice] → true → passes ✓
  → LiquidityLib.addLiquidity triggers metricOmmModifyLiquidityCallback on Bob → Bob pays token0/token1
  → Alice receives the LP position
  → Bob has deposited into the restricted pool without being on the allowlist ✓

Step 3 (full bypass with collusion):
  Alice calls pool.removeLiquidity(owner=Alice, ...)  // passes msg.sender == owner check at L206
  → Alice receives token0/token1 back
  → Alice transfers proceeds to Bob off-chain
  → Net effect: Bob deposited and withdrew from a pool he was explicitly barred from ✓
```

**Relevant code locations:**
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L204-206)
```text
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```
