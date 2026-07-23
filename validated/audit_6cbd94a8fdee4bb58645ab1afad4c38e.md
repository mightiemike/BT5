Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and gates deposits by checking the LP position `owner` argument instead. Because `owner` is a caller-supplied calldata value, any unprivileged address can bypass the allowlist by calling `addLiquidity` with an allowlisted address as `owner`, directly contradicting the extension's stated purpose of gating deposits by depositor address.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as two distinct values to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension hook. `DepositAllowlistExtension.beforeAddLiquidity` then silently discards `sender` (the first `address` parameter is unnamed) and checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual initiator):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The inconsistency is the root cause: the deposit guard checks who *owns* the resulting position, not who *provides the tokens and triggers the deposit*. Since `owner` is freely chosen by the caller, the allowlist check is trivially bypassed.

## Impact Explanation
This is an admin-boundary break: the pool admin's allowlist configuration is bypassed by an unprivileged path. Any EOA or contract can deposit into a restricted pool by supplying an allowlisted address as `owner`. The pool admin's access-control invariant ("only allowlisted depositors may add liquidity") is fully negated. Additionally, the allowlisted address receives an unwanted LP position it did not request, exposing it to impermanent loss on tokens it never chose to commit.

## Likelihood Explanation
The attack requires no special privilege, no malicious token, and no admin cooperation. Any caller can execute it by supplying any on-chain allowlisted address as `owner` in a standard `addLiquidity` call. The only cost to the attacker is gas and the deposited tokens (which are credited to the allowlisted owner's position). The attack is repeatable on every pool using this extension.

## Recommendation
Replace the `owner` check with `sender` in `beforeAddLiquidity`, mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
// Before (incorrect):
function beforeAddLiquidity(address, address owner, ...) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
function beforeAddLiquidity(address sender, address, ...) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` configured on `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(bob, alice, salt, deltas, extensionData)` — `sender=bob`, `owner=alice`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` executes; the liquidity callback fires against `msg.sender` (Bob), pulling Bob's tokens.
7. Alice's position is credited with the LP shares.
8. Bob has lost his tokens; Alice holds an unwanted position; the allowlist has been fully bypassed. [1](#0-0) [2](#0-1) [3](#0-2)

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
