Audit Report

## Title
`DepositAllowlistExtension::beforeAddLiquidity` validates position recipient instead of token payer, allowing non-allowlisted actors to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` argument (the position recipient), but `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (owner-overload) allows any caller to supply an arbitrary `owner` while `payer` is always hardcoded to `msg.sender`. Because the allowlist check validates the recipient rather than the token source, any non-allowlisted actor can bypass the guard by naming an allowlisted address as `owner`.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives `owner` as its second argument and gates on it: [1](#0-0) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (owner-overload, L56–68) explicitly separates `owner` (caller-controlled position recipient) from `payer` (always `msg.sender`): [2](#0-1) 

`_validateOwner` only rejects `address(0)`, imposing no allowlist constraint on the supplied `owner`: [3](#0-2) 

The callback pulls tokens from `payer` (the real depositor, `msg.sender` of the original call), not from `owner`: [4](#0-3) 

`pay` calls `safeTransferFrom(payer, recipient, value)` when `payer != address(this)`, confirming tokens are pulled from the real caller: [5](#0-4) 

The allowlist check therefore validates the **position recipient** (`owner`), not the **token source** (`payer = msg.sender`). These are two independent, caller-controlled addresses.

## Impact Explanation

The deposit allowlist — the pool admin's primary access-control mechanism — is rendered entirely ineffective. Any non-allowlisted actor can deposit into a restricted pool by supplying an allowlisted address as `owner`. The non-allowlisted actor's tokens are pulled and the allowlisted address receives LP shares it never requested. This breaks the core admin-boundary invariant ("only allowlisted addresses may deposit") and additionally griefs the named `owner` by forcing unwanted pool exposure. Severity: **Medium** — no direct loss of existing LP principal, but the core access-control mechanism is completely bypassed by any unprivileged actor.

## Likelihood Explanation

- Requires no special privilege; `addLiquidityExactShares` is a public `external payable` function.
- The only prerequisite is knowing one allowlisted address, which is publicly readable from `allowedDepositor`.
- Exploitable on every pool that uses `DepositAllowlistExtension` with a non-open allowlist (`allowAllDepositors[pool] == false`).
- Repeatable at will with no cooldown or rate limit.

## Recommendation

The hook must validate the **actual depositor** (the address whose tokens are pulled), not the position recipient. The simplest correct fix is to pass `payer = msg.sender` in `extensionData` inside `_addLiquidity` and decode it in the hook:

```solidity
// In MetricOmmPoolLiquidityAdder._addLiquidity, prepend payer to extensionData
// passed to the pool's addLiquidity call.

// In DepositAllowlistExtension.beforeAddLiquidity:
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

Alternatively, restrict the owner-overload of `addLiquidityExactShares` so that `owner` must equal `msg.sender`, eliminating the owner/payer split entirely for allowlisted pools.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false  // Bob is NOT allowlisted

Attack:
  vm.startPrank(bob);
  token0.approve(address(liquidityAdder), type(uint256).max);
  token1.approve(address(liquidityAdder), type(uint256).max);

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
  // - Bob's tokens were pulled (payer = bob = msg.sender)
  // - Alice received LP shares she never requested
  // - Bob (non-allowlisted) successfully deposited into a restricted pool
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
