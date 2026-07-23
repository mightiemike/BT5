Audit Report

## Title
Allowlist Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass Deposit Restriction — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual pool caller, passed as `msg.sender` by the pool) and instead validates `owner` (the freely-chosen LP position recipient). Because `owner` is an arbitrary caller-supplied argument with no access control, any non-allowlisted address can bypass the deposit gate by nominating any allowlisted address as `owner`.

## Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with the actual caller as the first argument: [1](#0-0) 

The interface defines both `sender` and `owner` as distinct named parameters: [2](#0-1) 

The extension implementation discards `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

**Exploit path:**
1. Attacker (not allowlisted) reads any allowlisted address `A` from the public `allowedDepositor` mapping or `AllowedToDepositSet` events.
2. Attacker calls `pool.addLiquidity(owner=A, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=A, ...)`.
4. Extension evaluates `allowedDepositor[pool][A]` → `true` → passes without reverting.
5. Pool mints LP shares to `A`; the liquidity callback pulls tokens from the attacker.
6. Deposit succeeds despite the attacker never being on the allowlist.

The `isAllowedToDeposit` view helper also uses `owner`-keyed logic, consistent with the same root cause: [4](#0-3) 

## Impact Explanation

The deposit allowlist is completely defeated. Any unprivileged EOA or contract can add liquidity to a restricted pool by nominating any known allowlisted address as `owner`. The pool admin's access control intent — KYC gating, whitelist-only pools, regulatory restrictions — is rendered entirely ineffective. This constitutes broken core pool functionality: the guard the pool was configured to enforce does not gate the economically relevant actor (the payer/caller). Severity: **High** — access control bypass with direct impact on pool deposit restrictions.

## Likelihood Explanation

Exploitable by any EOA or contract with zero special privileges. The only precondition is knowledge of one allowlisted address for the target pool, which is trivially obtainable from the public `allowedDepositor` mapping or emitted `AllowedToDepositSet` events. No flash loan, reentrancy, or privileged role is required. The attack is repeatable indefinitely.

## Recommendation

Replace the `owner` check with the `sender` parameter in `beforeAddLiquidity`:

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

This ensures the check gates the address that actually initiates and pays for the deposit, not the LP position recipient.

## Proof of Concept

```solidity
function test_allowlistBypass_ownerVsSender() public {
    address allowedLP = makeAddr("allowedLP");
    address attacker  = makeAddr("attacker");
    extension.setAllowedToDeposit(address(pool), allowedLP, true);

    assertFalse(extension.allowedDepositor(address(pool), attacker));

    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);
    // Should revert — does NOT, because extension checks owner=allowedLP, not sender=attacker
    pool.addLiquidity(allowedLP, salt, deltas, callbackData, "");
    vm.stopPrank();

    // LP shares minted to allowedLP despite attacker never being allowlisted
    assertGt(stateView.positionBinShares(address(pool), allowedLP, salt, binIdx), 0);
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
