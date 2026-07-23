Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on caller-supplied `owner` instead of actual depositor `sender`, allowing full allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the pool call who supplies tokens) and instead checks the caller-supplied `owner` parameter against the allowlist. Because `owner` is a free parameter chosen by the caller in `MetricOmmPool.addLiquidity`, any address can bypass the deposit allowlist by nominating any already-authorized address as `owner`. The pool admin's intent — restricting which addresses may deposit capital — is completely defeated.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
  BEFORE_ADD_LIQUIDITY_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

The extension receives `(address sender, address owner, ...)`. `sender` is the actual `msg.sender` of the pool call — the address that will supply tokens via the liquidity callback. `owner` is a free parameter chosen by the caller.

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (unnamed) and gates only on `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The wrong value checked is `allowedDepositor[pool][owner]` — it should be `allowedDepositor[pool][sender]`. The analogous `SwapAllowlistExtension.beforeSwap` correctly gates on `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Exploit path:
1. Pool is deployed with `DepositAllowlistExtension`; `allowedDepositor[pool][Alice] = true`; Bob is not on the allowlist.
2. Bob calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. `beforeAddLiquidity` is invoked with `sender = Bob` (unnamed, ignored), `owner = Alice`. The check evaluates `allowedDepositor[pool][Alice]` → `true`. No revert.
4. Bob's callback transfers tokens; Alice receives the liquidity position.
5. The deposit allowlist is bypassed without any privileged action.

No existing guard prevents this: the `nonReentrant` modifier only prevents reentrancy, and there is no other check on `msg.sender` identity in the `addLiquidity` path.

## Impact Explanation
This is an admin-boundary break: the pool admin configures a deposit allowlist to restrict which addresses may supply capital to the pool. The bug allows any unprivileged address to bypass this control entirely by nominating any authorized address as `owner`. The unauthorized depositor supplies tokens and the authorized address receives the position. The pool admin's security boundary is completely defeated with a single public call.

## Likelihood Explanation
The bypass requires only a single public call to `addLiquidity` with a known authorized address as `owner`. The `allowedDepositor` mapping is public, so any observer can identify authorized addresses. No special permissions, flash loans, oracle manipulation, or privileged setup are needed. The attack is immediately executable by any address.

## Recommendation
Replace the unnamed first parameter with `sender` and gate on it instead of `owner`, consistent with `SwapAllowlistExtension`:

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
1. Deploy pool with `DepositAllowlistExtension`; call `setAllowedToDeposit(pool, Alice, true)`. Bob is not allowlisted.
2. Bob deploys a contract implementing the liquidity callback that transfers the required tokens.
3. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas, callbackData, extensionData)`.
4. `beforeAddLiquidity(sender=Bob [unnamed/ignored], owner=Alice, ...)` evaluates `allowedDepositor[pool][Alice]` → `true`. No revert.
5. Bob's callback executes; tokens are transferred from Bob; Alice receives the liquidity position.
6. Deposit allowlist bypassed with zero privileged actions.

Foundry test: assert that a non-allowlisted address calling `addLiquidity` with an allowlisted `owner` succeeds (no `NotAllowedToDeposit` revert), and that the same address calling with itself as `owner` reverts — demonstrating the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
