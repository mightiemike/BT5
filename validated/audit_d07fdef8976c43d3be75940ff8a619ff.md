Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on Caller-Supplied `owner` Instead of `sender`, Fully Bypassing the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead gates on `owner`, a free parameter chosen by the caller. Because any address can supply an allowlisted address as `owner`, the deposit allowlist is completely ineffective: any unprivileged caller can deposit into a pool the admin intended to restrict.

## Finding Description
`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, passing the real caller as the first argument and the LP-position recipient as the second.

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension:
```solidity
// metric-core/contracts/ExtensionCalling.sol:95-98
_callExtensionsInOrder(
  BEFORE_ADD_LIQUIDITY_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as the first argument but leaves it unnamed and unused, then checks `owner`:
```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free parameter supplied by the attacker. The guard passes whenever `allowedDepositor[pool][owner]` is true, regardless of who the actual caller is. The sister extension `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender` instead, confirming the discarded argument is a bug, not a design choice. The admin setter naming (`depositor`, `isAllowedToDeposit(pool_, depositor)`) further confirms the intent is to gate the actual depositing address.

## Impact Explanation
The deposit allowlist invariant — "only approved depositors may add liquidity" — is broken for every pool that deploys this extension. Any unprivileged address can deposit into a restricted pool by supplying any allowlisted address as `owner`. The attacker pays tokens via the callback and the allowlisted address receives LP shares. The pool admin has no mechanism to enforce which addresses may actually deposit. This constitutes broken core pool functionality (access control on liquidity provision) with direct fund-flow consequences, meeting the Medium threshold.

## Likelihood Explanation
The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can craft a standard `addLiquidity` call with a chosen `owner`. The official `MetricOmmPoolLiquidityAdder` router already demonstrates the exact pattern (depositing on behalf of another `owner`) in its own test suite, confirming the path is reachable in normal usage. The bypass is repeatable and unconditional.

## Recommendation
Name the first argument `sender` and gate on it, mirroring `SwapAllowlistExtension`:
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
If the intent is to allow trusted routers to deposit on behalf of allowlisted owners, a two-level check (`allowedDepositor[pool][sender] || allowedDepositor[pool][owner]`) should be made explicit and documented.

## Proof of Concept
```
Precondition: pool has DepositAllowlistExtension configured.
              allowedDepositor[pool][alice] = true.
              attacker is NOT on the allowlist.

1. attacker calls pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)
2. Pool calls _beforeAddLiquidity(attacker, alice, ...)
3. Extension checks allowedDepositor[pool][alice] == true → no revert
4. LiquidityLib.addLiquidity credits LP shares to alice
5. Pool calls attacker.metricOmmModifyLiquidityCallback → attacker pays tokens
6. Attacker has deposited into a restricted pool; allowlist guard was never triggered.
```
A Foundry test can reproduce this by deploying the extension, allowlisting only `alice`, then calling `addLiquidity` from an unlisted `attacker` address with `owner = alice` and asserting no revert occurs.