Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unapproved caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is designed to gate `addLiquidity` calls to approved depositors. Its `beforeAddLiquidity` hook receives `sender` (the actual `msg.sender`) as its first argument but silently discards it (unnamed `address`) and checks only `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` requirement, any unapproved operator can call `addLiquidity(approvedOwner, ...)` and pass the gate by naming an allowlisted address as `owner`.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly encodes both into the call: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but names it `_` (unnamed, discarded) and checks only `owner`: [3](#0-2) 

The guard `allowedDepositor[msg.sender][owner]` checks whether the **position recipient** is approved, not whether the **actual caller** is approved. `addLiquidity` has no `msg.sender == owner` guard (contrast with `removeLiquidity` which does): [4](#0-3) 

Exploit path:
1. Pool admin calls `setAllowedToDeposit(pool, victim_owner, true)` — `victim_owner` is on the allowlist.
2. Unapproved `operator` (not in `allowedDepositor`) calls `pool.addLiquidity(victim_owner, salt, deltas, callbackData, "")`.
3. Pool calls `_beforeAddLiquidity(msg.sender=operator, owner=victim_owner, ...)`.
4. Extension receives `(operator, victim_owner, ...)`, discards `operator`, checks `allowedDepositor[pool][victim_owner]` → `true`.
5. Gate passes. Operator's deposit succeeds. Operator pays tokens via callback and receives LP shares credited to `victim_owner`.

No existing guard prevents this. The `allowedDepositor` mapping is public, so any on-chain observer can enumerate approved addresses.

## Impact Explanation
The deposit allowlist — the sole access-control mechanism of `DepositAllowlistExtension` — is completely bypassed. A pool configured to accept deposits only from KYC'd or whitelisted addresses will accept deposits from any arbitrary caller, as long as they name an approved address as `owner`. This breaks the core functionality the extension exists to provide, constituting broken core pool functionality (access-control bypass on the liquidity provision path).

## Likelihood Explanation
The bypass requires only a direct call to `pool.addLiquidity(approvedOwner, ...)` from any EOA or contract. No privileged access, no special setup, and no non-standard token behavior is needed. The approved owner's address is readable from the public `allowedDepositor` mapping. Likelihood is high.

## Recommendation
Replace the `owner` check with the `sender` argument (the first, currently unnamed parameter):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to gate on the position owner (e.g., KYC on position holders), the current `owner` check is correct but the extension must also enforce `sender == owner` or explicitly document that operator-on-behalf-of-owner is an accepted pattern.

## Proof of Concept

```solidity
// Foundry test sketch
function test_operatorBypassesAllowlist() public {
    // owner is allowlisted, operator is not
    depositExt.setAllowedToDeposit(address(pool), owner, true);

    // operator calls addLiquidity naming owner as position recipient
    vm.prank(operator); // operator NOT in allowedDepositor
    pool.addLiquidity(owner, 0, deltas, callbackData, "");
    // succeeds — allowlist gate is bypassed because extension checks owner, not operator
}
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
