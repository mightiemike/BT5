Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates LP Position Recipient Instead of Actual Depositor, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates `owner` (the LP position recipient, a free caller-controlled parameter). Any address excluded from the allowlist can bypass the gate by naming any allowlisted address as `owner`, causing the extension's access control to be entirely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` invokes the hook with the actual caller as the first argument: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both `sender` and `owner` to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, …)` but names the first parameter `_` (unnamed/discarded) and checks only `owner`: [3](#0-2) 

The allowlist setter correctly maps `depositor → bool` per pool: [4](#0-3) 

Because `owner` is a free parameter in `addLiquidity`, any caller can pass an allowlisted address as `owner`. The hook evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` and permits the deposit. The actual token transfer originates from the unauthorized `msg.sender` via the liquidity callback, so the allowlist is fully circumvented. The `removeLiquidity` check enforces `msg.sender == owner`: [5](#0-4) 

This means the deposited tokens are credited to the allowlisted `owner`'s LP position, not the attacker's — but if the attacker controls an allowlisted contract (e.g., a purpose-built wrapper they had the pool admin allowlist), they can complete the full round-trip: deposit via unauthorized EOA → withdraw via the controlled allowlisted contract.

## Impact Explanation
The deposit allowlist — intended to enforce regulatory, compliance, or pool-composition controls — is rendered entirely ineffective. Any excluded address can inject liquidity into the pool at will. If the attacker controls an allowlisted address, they can recover the deposited tokens autonomously, constituting a direct bypass of an admin-enforced access control boundary. This breaks core pool functionality (gated liquidity provision) and constitutes an admin-boundary break where an unprivileged actor circumvents a pool admin-configured restriction.

## Likelihood Explanation
Exploitation requires no special privilege, no price manipulation, and no flash loan. Any caller of `addLiquidity` can supply an arbitrary `owner` address. The allowlisted address need not cooperate for the deposit to succeed. The bypass is unconditional and repeatable by any address.

## Recommendation
Name and validate the `sender` parameter (the actual depositor) instead of `owner`:

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

`sender` is `msg.sender` of the originating `addLiquidity` call — the address that transfers tokens via the callback — which is the correct entity to gate.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. `beforeAddLiquidity` is invoked with `sender = Bob` (discarded), `owner = alice`.
5. Hook checks `allowedDepositor[pool][alice]` → `true`. No revert.
6. Bob's tokens are pulled via the liquidity callback; Alice's LP position is credited.
7. The deposit allowlist is bypassed without any privileged access.

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
