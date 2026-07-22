### Title
`DepositAllowlistExtension` Checks Position Owner Instead of Actual Depositor, Allowing Unauthorized Liquidity Injection - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position recipient) against the allowlist, while silently ignoring the `sender` (the actual caller who pays tokens). Any unprivileged address can bypass the deposit allowlist by calling `addLiquidity` with `owner` set to any allowlisted address, injecting unauthorized liquidity into a curated pool.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two actor addresses: the unnamed first parameter (`sender`, the actual `msg.sender` of the pool's `addLiquidity` call) and `owner` (the position recipient). The guard discards `sender` entirely and only checks `owner`:

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

The pool passes both actors correctly through `ExtensionCalling._beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [3](#0-2) 

`sender` is the economic actor — the address that calls `addLiquidity` and settles the token payment in the callback. `owner` is merely the position recipient. The allowlist is intended to gate the depositor (the payer), but it gates the position recipient instead.

An unauthorized caller can:
1. Pick any allowlisted address as `owner`.
2. Call `pool.addLiquidity(allowlistedOwner, salt, deltas, callbackData, extensionData)` directly, or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedOwner, ...)`.
3. The extension evaluates `allowedDepositor[pool][allowlistedOwner]` → `true` → passes.
4. The unauthorized caller pays tokens via the modify-liquidity callback.
5. LP shares are credited to `allowlistedOwner`; the curated pool has accepted liquidity from an unauthorized source. [4](#0-3) 

The `_validateOwner` check in `MetricOmmPoolLiquidityAdder` only rejects `address(0)`, imposing no allowlist constraint on the caller: [5](#0-4) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), not the recipient:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [6](#0-5) 

The deposit extension applies the opposite (wrong) binding.

---

### Impact Explanation

The deposit allowlist is an admin-configured guard intended to restrict which addresses may inject liquidity into a curated pool. Because the guard checks the wrong actor, it is completely ineffective: any unprivileged address can deposit into a curated pool by naming any allowlisted address as `owner`. This constitutes an admin-boundary break — an unprivileged path bypasses a factory/pool admin-configured access control — and breaks the core pool functionality of curated liquidity management. The unauthorized depositor can alter the pool's bin liquidity distribution, affecting pricing and LP composition, without the pool admin's consent.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no oracle manipulation. Any externally-owned account can call `pool.addLiquidity` directly with a publicly-observable allowlisted address as `owner`. The allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events. Likelihood is **High**.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which gates on `sender`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with DepositAllowlistExtension configured.
// allowedDepositor[pool][alice] = true  (alice is allowlisted)
// bob is NOT allowlisted

function test_depositAllowlistBypass() public {
    // Alice is allowlisted; confirm bob is not
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), bob));
    assertTrue(depositExtension.isAllowedToDeposit(address(pool), alice));

    // Bob calls addLiquidity with owner = alice (an allowlisted address)
    // Extension checks allowedDepositor[pool][alice] → true → passes
    // Bob pays tokens; alice receives LP shares
    vm.startPrank(bob);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // This should revert (bob is not allowlisted) but it does NOT
    pool.addLiquidity(
        alice,          // owner = allowlisted address
        0,              // salt
        deltas,
        abi.encode(KIND_PAY),
        ""
    );
    vm.stopPrank();

    // Bob successfully injected liquidity into the curated pool
    // Alice has LP shares she never requested; pool accepted unauthorized deposit
    assertGt(pool.positionBinShares(alice, 0, 0), 0);
}
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
