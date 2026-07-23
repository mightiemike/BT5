Audit Report

## Title
`DepositAllowlistExtension` Validates `owner` Instead of `sender`, Allowing Any Unprivileged Actor to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the pool call) and validates only the caller-supplied `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` imposes no constraint that `owner == msg.sender`, any non-allowlisted actor can bypass the guard by passing any allowlisted address as `owner`, completely defeating the deposit access-control invariant.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a free `owner` parameter with no requirement that it equals `msg.sender`, and forwards both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

The only `owner == msg.sender` check in the pool exists only in `removeLiquidity`, not in `addLiquidity`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` names the first parameter `address` (unnamed, discarded) and validates only `owner`: [3](#0-2) 

The NatDoc at line 11 states the contract "Gates `addLiquidity` by **depositor** address", yet the depositor (`sender`) is silently discarded. The sibling `SwapAllowlistExtension.beforeSwap` correctly validates `sender`: [4](#0-3) 

The asymmetry confirms `DepositAllowlistExtension` diverges from both its own documentation and the established pattern.

## Impact Explanation

A pool admin deploys `DepositAllowlistExtension` to restrict which addresses may provide liquidity (e.g., KYC/regulatory compliance). The guard is entirely ineffective: a non-allowlisted attacker passes any allowlisted address as `owner`, the extension checks that allowlisted address and passes, and the attacker's tokens enter the pool. With a colluding allowlisted address, the attacker can recover proceeds via `removeLiquidity`. This constitutes a broken core pool functionality / admin-boundary break: unapproved capital freely enters the pool, unapproved LP fees are earned, and the pool admin's access-control invariant is completely defeated.

## Likelihood Explanation

Medium. Allowlisted addresses are visible on-chain via emitted `AllowedToDepositSet` events. Any actor who can read chain state can identify a valid `owner` to pass. The attack requires no special privilege, no flash loan, and no exotic token behavior — only the ability to call `addLiquidity` with an arbitrary `owner`.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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
```

If the intended semantics are to gate by position owner rather than depositor, the NatDoc, mapping key name (`allowedDepositor`), and design rationale must be corrected.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true   // alice is KYC'd
  bob is NOT allowlisted

Attack:
  vm.prank(bob);
  pool.addLiquidity(
      owner        = alice,   // allowlisted → guard passes
      salt         = 0,
      deltas       = <valid bins>,
      callbackData = "",      // bob's callback pays tokens
      extensionData= ""
  );
  // beforeAddLiquidity receives sender=bob, owner=alice
  // checks allowedDepositor[pool][alice] → true → no revert
  // Bob's tokens enter pool; position credited to alice

  vm.prank(alice);            // alice (colluding) removes
  pool.removeLiquidity(alice, 0, deltas, "");
  // alice returns proceeds to bob off-chain

Result: bob (non-allowlisted) has effectively provided liquidity and
        earned LP fees, bypassing the deposit allowlist entirely.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
