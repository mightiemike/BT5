The bug is confirmed. Here is the full trace:

**Call chain:**
1. `MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`. [1](#0-0) 
2. `ExtensionCalling._beforeAddLiquidity` correctly encodes both `sender` and `owner` into the hook call. [2](#0-1) 
3. `DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`sender`) — it is unnamed — and checks only `owner`. [3](#0-2) 

The guard at line 38 is `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool (correct key) and `owner` is the position recipient — **not** the actual depositing address. [4](#0-3) 

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of `sender`, allowing any non-allowlisted operator to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity` hook ignores the `sender` argument (the actual caller) and instead checks whether the `owner` argument (the position recipient) is allowlisted. Because `addLiquidity` accepts an arbitrary `owner` parameter with no restriction on who may supply it, any non-allowlisted address can bypass the gate by passing an allowlisted address as `owner`.

### Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and passes `msg.sender` as `sender` to the hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension receives both values but discards `sender` (unnamed first parameter):

```solidity
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The check `allowedDepositor[pool][owner]` passes whenever the supplied `owner` is allowlisted, regardless of who is actually calling `addLiquidity`. An attacker simply sets `owner = allowlisted_address` and the gate is cleared.

### Impact Explanation
The deposit allowlist is completely defeated. Any address — including one that was explicitly denied — can deposit into the pool by nominating an allowlisted address as the position owner. The attacker supplies the tokens (via the liquidity callback) and the position accrues to the nominated owner, but the pool's intended access restriction is fully bypassed. Pools relying on this extension for KYC/compliance or whitelist-only liquidity provision have no effective gate on who can deposit.

### Likelihood Explanation
The attack requires no privileged access, no special token behavior, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity` with an allowlisted `owner`. The only prerequisite is knowing one allowlisted address, which is readable from `allowedDepositor` (public mapping) or from emitted `AllowedToDepositSet` events.

### Recommendation
Replace the `owner` check with the `sender` argument:

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

### Proof of Concept
```solidity
// Foundry test sketch
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
