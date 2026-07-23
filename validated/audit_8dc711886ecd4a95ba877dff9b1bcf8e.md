Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead gates on `owner`, a freely-chosen caller parameter that designates the LP-share recipient. Because `owner` is attacker-controlled and the `allowedDepositor` mapping is public, any unprivileged address can bypass the allowlist by supplying any already-allowlisted address as `owner`. This completely defeats the pool admin's access control over who may inject liquidity into a permissioned pool.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument and the caller-supplied `owner` as the second:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both verbatim:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` names the first argument `address` (unnamed, discarded) and gates on the second (`owner`):

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
``` [3](#0-2) 

The contract's own NatSpec states it "Gates `addLiquidity` by depositor address": [4](#0-3) 

But the implementation checks `owner` (LP-share recipient), not `sender` (the actual depositor). The `allowedDepositor` mapping is public, so any attacker can read which addresses are allowlisted: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator), confirming the asymmetry is a bug: [6](#0-5) 

## Impact Explanation
This is an **admin-boundary break**: a pool admin who deploys `DepositAllowlistExtension` to create a permissioned liquidity pool receives no protection. Any unprivileged address can inject arbitrary liquidity into the pool, altering bin balances and pool state, bypassing the core liquidity-management invariant for permissioned pools. The LP shares are minted to the allowlisted `owner`, not the attacker, so the attacker forfeits their tokens — but the guard's purpose (restricting who may alter pool liquidity) is completely defeated.

## Likelihood Explanation
Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with a crafted `owner` argument. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping. This is trivially satisfied by reading on-chain state.

## Recommendation
Replace the unnamed first argument with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

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
```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension as EXTENSION_1
  admin = pool admin
  alice = allowlisted depositor  (allowedDepositor[pool][alice] = true)
  eve   = unprivileged address   (allowedDepositor[pool][eve]   = false)

Attack:
  eve calls pool.addLiquidity(
      owner        = alice,   // allowlisted; check passes
      salt         = 0,
      deltas       = <valid delta>,
      callbackData = "",
      extensionData= ""
  );

  Inside beforeAddLiquidity:
    msg.sender = pool
    owner      = alice  → allowedDepositor[pool][alice] == true → no revert

  Result:
    - Eve's tokens are pulled via the liquidity callback (eve pays).
    - LP shares are minted to alice.
    - Eve has bypassed the deposit allowlist entirely.
    - The pool now holds liquidity from an unauthorized depositor.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-11)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
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
