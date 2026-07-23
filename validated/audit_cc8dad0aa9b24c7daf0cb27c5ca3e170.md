Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks LP recipient (`owner`) instead of actual depositor (`sender`), rendering the deposit allowlist bypassable — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` parameter and checks `owner` (the LP recipient) instead. Because `owner` is a free caller-supplied parameter independent of who provides the tokens, any address not on the allowlist can bypass the guard entirely by naming any allowlisted address as `owner`.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the extension hook with `msg.sender` as `sender` and the user-supplied `owner` as the LP recipient: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both addresses verbatim to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both addresses but the first parameter (`sender`) is unnamed and discarded; the allowlist lookup is performed on `owner`: [3](#0-2) 

The contract's NatSpec and mapping name both declare the guarded entity to be the *depositor* — the address that actually calls `addLiquidity` and pays tokens: [4](#0-3) 

The wrong value checked is `allowedDepositor[msg.sender][owner]` when it should be `allowedDepositor[msg.sender][sender]`. Because `owner` is freely chosen by the caller, the check is trivially satisfied by any attacker who knows a single allowlisted address.

## Impact Explanation

The deposit allowlist is rendered completely ineffective. Any unprivileged address can add liquidity to a pool whose admin intended to restrict deposits (e.g., KYC/compliance, curated LP set, or protocol-only liquidity). This constitutes a broken core pool access-control invariant: *only addresses explicitly permitted by the pool admin may add liquidity to a deposit-restricted pool*. The attacker can place liquidity in arbitrary bins, altering bin distribution and affecting swap routing and fee accrual for existing LPs. The authorized `owner` address receives LP shares it never requested, exposing it to pool risk and requiring active management to remove.

## Likelihood Explanation

Medium. Authorized addresses are typically public (known market makers, protocol multisigs, or addresses visible on-chain from prior deposits). An attacker needs only one such address. No special privilege, flash loan, or oracle manipulation is required — a single `addLiquidity` call suffices. The bypass is repeatable and unconditional whenever the extension is configured.

## Recommendation

Name and check `sender` (the actual depositor) instead of `owner` (the LP recipient):

```solidity
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

If the intent is to gate both the caller and the LP recipient, both should be checked explicitly.

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** on the allowlist.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(bob /*sender — unnamed, discarded*/, alice /*owner*/, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → returns success selector. `bob` is never checked.
6. `LiquidityLib.addLiquidity` executes; the token callback fires against Bob's address; Bob pays the tokens.
7. Alice receives LP shares she never requested; Bob has successfully deposited into a pool he is not authorized to touch.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-14)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
