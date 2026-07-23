Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on LP position `owner` instead of the actual depositor `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`, who pays tokens via callback) and instead checks the `owner` argument (the LP position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any caller, any address can bypass the deposit guard by naming an allowlisted address as `owner`. The pool admin's primary access-control mechanism for restricting pool participation is unconditionally broken.

## Finding Description
`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, passing the actual caller as `sender` and the explicit `owner` parameter as the position recipient: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender` (unnamed first parameter) and gates only on `owner`: [2](#0-1) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller of `pool.swap`): [3](#0-2) 

The exploit path is direct: Bob (not allowlisted) calls `pool.addLiquidity(owner=Alice, ...)` where Alice is allowlisted. The hook receives `sender=Bob, owner=Alice`, evaluates `allowedDepositor[pool][Alice] == true`, and does not revert. Bob's callback pays the tokens; Alice receives LP shares she did not request. The pool admin's intent to restrict who may deposit is fully bypassed without any privileged action.

The contract's own NatSpec states the intent is to gate by **depositor** address (`/// @notice Gates addLiquidity by depositor address, per pool`), confirming the check on `owner` is a bug, not a design choice: [4](#0-3) 

## Impact Explanation
This is an admin-boundary break: the pool admin's sole mechanism for restricting who may add liquidity to a guarded pool (KYC-gated, institutional, or whitelist-only) is unconditionally bypassed by any unprivileged caller. The attacker can force unwanted LP positions into any allowlisted address, exposing that address to impermanent loss in bins it did not choose, and complicating its position management. The allowlisted address can recover funds via `removeLiquidity` (which enforces `msg.sender == owner`), but the admin's access-control boundary is broken regardless.

## Likelihood Explanation
No special privilege is required. Any EOA or contract can call `pool.addLiquidity` directly with an arbitrary `owner`. The only precondition is knowing one allowlisted address, which is publicly readable from the `allowedDepositor` mapping or past `AllowedToDepositSet` events. The attacker bears the token cost, making this a griefing vector, but the bypass is unconditional and repeatable.

## Recommendation
Change `beforeAddLiquidity` to gate on `sender` (the actual depositor/payer) instead of `owner`, consistent with `SwapAllowlistExtension`:

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

If the intended semantic is to restrict LP ownership (not the payer), the NatSpec, event names, and mapping key must be updated to document `owner` as the gated entity, and the asymmetry with `SwapAllowlistExtension` must be explicitly justified.

## Proof of Concept
```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - Admin calls setAllowedToDeposit(pool, Alice, true)
  - Bob is NOT on the allowlist

Attack:
  1. Bob calls pool.addLiquidity(
         owner    = Alice,   // allowlisted → guard passes
         salt     = 99,
         deltas   = <valid bins>,
         callbackData = "",
         extensionData = ""
     )
  2. _beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...) is called.
  3. beforeAddLiquidity receives sender=Bob (discarded), owner=Alice.
     Check: allowedDepositor[pool][Alice] == true → no revert.
  4. Pool executes the deposit; Bob's callback pays the tokens.
  5. Alice holds LP shares in bins she never chose; Bob bypassed the allowlist.

Alternatively via MetricOmmPoolLiquidityAdder (no custom callback needed):
  Bob calls liquidityAdder.addLiquidityExactShares(
      pool, Alice, 99, deltas, maxAmount0, maxAmount1, ""
  )
  — same outcome; Bob is the payer, Alice is the owner, guard passes.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
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
