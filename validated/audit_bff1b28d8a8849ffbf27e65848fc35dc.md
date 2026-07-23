Audit Report

## Title
`DepositAllowlistExtension` Gates on Caller-Supplied `owner` Instead of `sender`, Fully Bypassing the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates the caller-supplied `owner` parameter against the allowlist. Because `owner` is a free input chosen by the caller, any unprivileged address can pass the allowlist check by naming any already-allowlisted address as `owner`. The deposit allowlist is rendered completely inoperative.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both `sender` and `owner` to the extension: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` names the first parameter (the actual depositor/`sender`) as unnamed/discarded, and gates exclusively on `owner`: [3](#0-2) 

The admin configures the allowlist with the intent of restricting the *depositor* (the actual caller): [4](#0-3) 

Since `owner` is a free parameter supplied by the caller, the guard is trivially bypassed. The asymmetry with `SwapAllowlistExtension`, which correctly checks `sender`, confirms the deposit check is wrong: [5](#0-4) 

## Impact Explanation

The deposit allowlist is rendered completely inoperative. Any unprivileged address can call `addLiquidity(allowlistedAddress, ...)` and the guard passes unconditionally. Pools relying on `DepositAllowlistExtension` for KYC gating, whitelist-only liquidity programs, or regulatory restrictions receive deposits from arbitrary actors, breaking the admin-enforced access control boundary without any privileged action. This is a direct admin-boundary break where an unprivileged path bypasses a pool admin-configured access control.

## Likelihood Explanation

Exploitation requires no special role, no privileged key, and no unusual token behavior. Any address that knows one allowlisted address — trivially readable from on-chain events or the public `allowedDepositor` mapping — can bypass the guard in a single transaction. The attack is repeatable indefinitely.

## Recommendation

Rename the first parameter to `sender` and gate on it instead of `owner`, mirroring `SwapAllowlistExtension`:

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`.
2. Bob (not allowlisted) calls `pool.addLiquidity(alice /*owner*/, salt, deltas, callbackData, extensionData)`.
3. `beforeAddLiquidity` receives `(sender=Bob, owner=Alice, ...)`, ignores `Bob`, checks `allowedDepositor[pool][Alice]` → `true` → no revert.
4. Bob's swap callback transfers Bob's tokens into the pool; Alice receives the LP shares.
5. Bob has deposited into a pool he is not authorized to touch. The allowlist provided zero protection.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
