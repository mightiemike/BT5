Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unlisted Address to Bypass the Deposit Gate - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` who pays tokens) and enforces the allowlist only against `owner` (the position beneficiary, a free caller-controlled argument). Any unlisted address can bypass the gate by supplying any allowlisted address as `owner`.

## Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the actual caller who pays tokens via callback; `owner` is the position beneficiary freely supplied by the caller. `DepositAllowlistExtension.beforeAddLiquidity` is declared with the first parameter unnamed and discarded:

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

The guard evaluates `allowedDepositor[pool][owner]` where `owner` is entirely attacker-controlled. The `SwapAllowlistExtension` correctly checks `sender` instead:

```solidity
function beforeSwap(address sender, address, ...)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }
``` [3](#0-2) 

The contract's own NatSpec states *"Gates `addLiquidity` by depositor address, per pool"* and the storage mapping is named `allowedDepositor`, confirming the intended subject is the depositor (`sender`), not the position beneficiary (`owner`). [4](#0-3) 

## Impact Explanation

A pool configured with `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC-gated or protocol-controlled depositors) provides zero effective restriction. Any unlisted address can deposit into a restricted pool by setting `owner` to any allowlisted address. The pool admin's core invariant — only allowlisted addresses may add liquidity — is completely broken by an unprivileged caller. This constitutes an admin-boundary break: a factory-configured access control is bypassed through a valid, non-privileged call path.

## Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no unusual token behavior. Any EOA or contract can trigger it in a single transaction by choosing any already-allowlisted address as `owner`. The bypass is deterministic and unconditional whenever the extension is deployed and a pool is configured to use it. The attacker pays tokens via the callback and the position is credited to the allowlisted `owner` address.

## Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist against it, consistent with the contract's stated purpose and the `SwapAllowlistExtension` pattern:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT in the allowlist

Attack:
  bob calls pool.addLiquidity(alice /*owner*/, salt, deltas, callbackData, "")
    → pool calls _beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)
    → extension discards bob (sender), checks allowedDepositor[pool][alice] == true ✓
    → deposit proceeds; bob pays tokens via callback; position credited to alice

Result:
  bob (unlisted) successfully deposited into a restricted pool.
  The deposit allowlist is fully bypassed.
```

Foundry test outline:
1. Deploy `DepositAllowlistExtension`, configure pool to use it.
2. Call `setAllowedToDeposit(pool, alice, true)`.
3. From `bob` (not allowlisted), call `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Assert the call succeeds and bob's tokens are transferred — confirming the bypass.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
