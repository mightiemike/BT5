Audit Report

## Title
`DepositAllowlistExtension::beforeAddLiquidity` checks position recipient (`owner`) instead of token payer, allowing non-allowlisted actors to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` argument (position recipient), but `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any caller to supply an arbitrary `owner` while `msg.sender` (the actual token payer) is stored separately in transient context and used to pull tokens in the callback. A non-allowlisted actor can pass any allowlisted address as `owner`, satisfy the allowlist check, and have their own tokens pulled — effectively depositing into a restricted pool without being allowlisted.

## Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)` where `msg.sender` is the LiquidityAdder contract and `owner` is the position recipient supplied by the original caller. [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores the first argument (`sender` = LiquidityAdder) and checks only `owner` against the allowlist: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (owner-overload) accepts a caller-controlled `owner` and hardcodes `payer = msg.sender`, passing them independently into `_addLiquidity`: [3](#0-2) 

`_validateOwner` only rejects `address(0)`, imposing no allowlist constraint: [4](#0-3) 

The transient pay context stores `payer = msg.sender` (the real depositor), and the callback pulls tokens from that address: [5](#0-4) 

The allowlist check therefore validates the **position recipient** (`owner = alice`), not the **token source** (`payer = bob`). These are two independent addresses with no binding enforced anywhere in the call path.

## Impact Explanation

A non-allowlisted actor (Bob) can deposit into any pool protected by `DepositAllowlistExtension` by supplying any allowlisted address (Alice) as `owner`. Bob's tokens are pulled; Alice receives LP shares she never requested. The pool admin's core access-control invariant — "only allowlisted addresses may deposit" — is completely broken. Every pool using this extension with a non-open allowlist is affected. Additionally, Alice receives unwanted LP exposure, which can be used to grief her. Severity: Medium — no direct loss of existing LP principal, but the deposit allowlist guard is rendered entirely ineffective.

## Likelihood Explanation

No special privilege is required. Any external actor can call `addLiquidityExactShares` with an arbitrary `owner`. The only prerequisite is knowing one allowlisted address, which is publicly readable from `allowedDepositor`. The attack is repeatable on every pool using `DepositAllowlistExtension` with a non-open allowlist.

## Recommendation

The hook must validate the **actual depositor** (the address whose tokens are pulled), not the position recipient. Since the real payer is not currently threaded through to the extension, the preferred fix is to encode `payer = msg.sender` into `extensionData` in the LiquidityAdder and decode it in the hook:

```solidity
function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata extData)
    external view override returns (bytes4)
{
    address depositor = abi.decode(extData, (address));
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][depositor]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Alternatively, check the pool's direct caller (`sender`) and require it to be an allowlisted router that enforces `owner == msg.sender` for direct deposits.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false  // Bob is NOT allowlisted

Attack:
  vm.startPrank(bob);
  token0.approve(address(liquidityAdder), MAX);
  token1.approve(address(liquidityAdder), MAX);

  // Bob sets owner = alice (allowlisted), payer = bob (msg.sender)
  liquidityAdder.addLiquidityExactShares(
      pool,
      alice,   // owner — passes allowlist check
      salt,
      deltas,
      max0,
      max1,
      ""
  );
  vm.stopPrank();

  // Result:
  // - beforeAddLiquidity checked allowedDepositor[pool][alice] → true → no revert
  // - Bob's tokens were pulled (payer = bob)
  // - Alice received LP shares
  // - Bob (non-allowlisted) successfully deposited into a restricted pool
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
