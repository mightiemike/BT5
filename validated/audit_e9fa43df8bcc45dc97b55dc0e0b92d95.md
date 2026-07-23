Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates the `owner` argument (the position recipient). Because `addLiquidity` accepts an arbitrary caller-supplied `owner` with no restriction, any non-allowlisted address can bypass the gate by passing an allowlisted address as `owner`.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly encodes both into the hook call: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both values but the first parameter (`sender`) is unnamed and silently discarded. The guard checks `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the position recipient — not the actual depositing address: [3](#0-2) 

**Exploit path:**
1. Pool admin allowlists `alice` via `setAllowedToDeposit(pool, alice, true)`.
2. Attacker (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Hook receives `(sender=attacker, owner=alice, ...)`, discards `sender`, checks `allowedDepositor[pool][alice]` → `true` → no revert.
4. Attacker provides tokens via the liquidity callback; position is minted to `alice`. The allowlist gate is fully cleared.

The `isAllowedToDeposit` view function also only checks the `depositor` key against the mapping, which is consistent with the intended design but the hook does not enforce it: [4](#0-3) 

## Impact Explanation
The deposit allowlist is completely defeated. Any address — including one explicitly denied — can deposit into any pool using this extension by nominating an allowlisted address as the position owner. Pools relying on this extension for KYC/compliance or whitelist-only liquidity provision have no effective gate on who can deposit. This constitutes a broken core pool access-control mechanism with direct fund-flow impact (unauthorized liquidity enters the pool).

## Likelihood Explanation
The attack requires no privileged access, no special token behavior, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity` with an allowlisted `owner`. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping or from emitted `AllowedToDepositSet` events. The attack is repeatable at zero marginal cost.

## Recommendation
Replace the `owner` check with the `sender` argument in `beforeAddLiquidity`:

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

Also update `isAllowedToDeposit`, `setAllowedToDeposit`, and the storage mapping name to reflect that the gated entity is the depositing caller, not the position owner.

## Proof of Concept
```solidity
function test_nonAllowlistedOperatorBypassesGate() public {
    address allowlistedOwner = makeAddr("allowlistedOwner");
    address attacker         = makeAddr("attacker");

    // Only allowlistedOwner is permitted
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), allowlistedOwner, true);

    // Attacker is NOT allowlisted
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Attacker calls addLiquidity with owner = allowlistedOwner
    // Extension checks allowedDepositor[pool][allowlistedOwner] → true → no revert
    vm.prank(attacker);
    pool.addLiquidity(allowlistedOwner, salt, deltas, callbackData, extensionData);
    // Deposit succeeds; attacker bypassed the allowlist
}
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
