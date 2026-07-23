### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented to gate `addLiquidity` by **depositor address**, but its `beforeAddLiquidity` hook silently drops the `sender` argument and checks `owner` (the LP-share recipient) instead. Any unprivileged address can bypass the allowlist by routing through `MetricOmmPoolLiquidityAdder` and supplying an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` discards the first parameter (`sender`) and gates on `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

Its sibling `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-share recipient:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly separates payer from owner — `msg.sender` pays, but an arbitrary `owner` receives the LP shares:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // ← caller-controlled, can be any allowlisted address
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [4](#0-3) 

**Attack path:**

1. Pool admin deploys pool with `DepositAllowlistExtension`; allowlists only `alice`.
2. Attacker (Bob, not allowlisted) calls `adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extensionData)`.
3. Adder calls `pool.addLiquidity(alice, salt, deltas, ...)` with `msg.sender = adder`.
4. Pool calls `_beforeAddLiquidity(adder, alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → passes.
6. Bob's tokens are pulled; Alice receives LP shares. The allowlist is fully bypassed.

Allowlisted addresses are publicly discoverable from the `AllowedToDepositSet` event emitted by `setAllowedToDeposit`. [5](#0-4) 

---

### Impact Explanation

The deposit allowlist — the only on-chain guard restricting who may provide liquidity to a restricted pool — is rendered ineffective. Any unprivileged address can inject liquidity into a pool the admin intended to gate, violating the admin-boundary invariant and breaking the core deposit-control flow. Depending on the pool's purpose (e.g., KYC-gated, whitelist-only LP programs), this allows unauthorized parties to acquire LP positions and participate in fee accrual and withdrawal flows they were explicitly excluded from.

---

### Likelihood Explanation

The bypass requires only: (a) knowledge of one allowlisted address (readable from on-chain events), and (b) a call to the publicly deployed `MetricOmmPoolLiquidityAdder`. No privileged access, flash loans, or special tokens are needed. Any pool that deploys `DepositAllowlistExtension` without `allowAllDepositors = true` is affected.

---

### Recommendation

Change `beforeAddLiquidity` to gate on `sender` (the actual caller/payer), consistent with how `SwapAllowlistExtension` gates on `sender`:

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

If the intent is instead to gate on the LP-share recipient (`owner`), the NatSpec, event names, and setter names must be updated to reflect that semantic, and the `addLiquidityExactShares(pool, owner, ...)` overload in `MetricOmmPoolLiquidityAdder` should be reconsidered, since it lets any caller assign LP shares to an allowlisted owner.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume: pool deployed with DepositAllowlistExtension
// alice is allowlisted; bob is not

// 1. Admin setup (legitimate)
extension.setAllowedToDeposit(address(pool), alice, true);
// bob is NOT allowlisted

// 2. Bob bypasses the allowlist via LiquidityAdder
vm.prank(bob);
adder.addLiquidityExactShares(
    address(pool),
    alice,          // owner = allowlisted address
    salt,
    deltas,
    max0,
    max1,
    ""
);
// Result: bob's tokens are pulled, alice receives LP shares,
// no revert — allowlist completely bypassed.

// 3. Confirm: direct call from bob (without adder) would revert
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
pool.addLiquidity(bob, salt, deltas, callbackData, "");
// This reverts because owner=bob is not allowlisted.
// But the adder path above succeeds — demonstrating the bypass.
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
