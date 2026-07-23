Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller who provides tokens) and instead checks `owner` (the LP position recipient). Any unprivileged address can bypass the deposit allowlist by naming an allowlisted address as `owner`. Simultaneously, a legitimately allowlisted depositor is blocked whenever their intended `owner` is not on the allowlist.

## Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, passing the actual caller as `sender` and the position recipient as `owner`. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both addresses faithfully to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, ...))`. [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (unnamed `address,`) and checks only `owner`: [3](#0-2) 

The admin setter confirms the intended entity is the depositor/caller, not the owner: [4](#0-3) 

The parallel `SwapAllowlistExtension.beforeSwap` correctly names `sender` as the first parameter and discards the second, demonstrating the intended pattern: [5](#0-4) 

The two hooks are structurally symmetric but the checked parameter is swapped in `DepositAllowlistExtension`.

## Impact Explanation

Two broken invariants arise simultaneously. First, **allowlist bypass**: any unprivileged address `bob` calls `pool.addLiquidity(owner=alice, ...)` where `alice` is allowlisted. The hook sees `owner=alice`, the check passes, and `bob`'s tokens enter the pool via the callback while `alice` receives LP shares she never requested. The pool admin's access-control boundary is fully bypassed. Second, **legitimate depositor DoS**: allowlisted address `alice` calls `addLiquidity(owner=vault, ...)` where `vault` is not on the allowlist; the hook reverts with `NotAllowedToDeposit` even though `alice` is authorized. Both broken invariants constitute an admin-boundary break allowing unauthorized liquidity injection into restricted pools, diluting existing LP fee shares and altering bin distributions without the pool admin's consent.

## Likelihood Explanation

The bypass requires no special privileges, no flash loans, and no oracle manipulation. Any EOA or contract can call `addLiquidity` on a pool with this extension active, pass any allowlisted address as `owner`, and the guard passes unconditionally. The allowlisted address need not cooperate. The DoS path is equally trivial: any allowlisted depositor directing their position to a non-allowlisted recipient (a common pattern with routers or vaults) is silently blocked.

## Recommendation

Replace the unnamed `address,` with the named `sender` parameter and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

## Proof of Concept

```
Setup:
  Pool P configured with DepositAllowlistExtension E.
  Pool admin calls E.setAllowedToDeposit(P, alice, true).
  bob is NOT on the allowlist.

Attack:
  bob calls P.addLiquidity(
      owner        = alice,   // allowlisted — passes the (wrong) check
      salt         = 0,
      deltas       = <valid deltas>,
      callbackData = <bob's callback that transfers tokens>,
      extensionData = ""
  );

Result:
  E.beforeAddLiquidity(bob /*sender, ignored*/, alice /*owner, checked*/) → passes.
  bob's tokens are pulled via metricOmmSwapCallback.
  alice receives LP shares she never requested.
  bob has bypassed the deposit allowlist entirely.

Legitimate depositor DoS:
  alice calls P.addLiquidity(owner = vault, ...) where vault ∉ allowlist.
  E.beforeAddLiquidity(alice /*ignored*/, vault /*checked*/) → reverts NotAllowedToDeposit.
  alice, though allowlisted, cannot deposit to her own vault.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

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
