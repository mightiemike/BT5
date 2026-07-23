### Title
SwapAllowlistExtension Bypass via Router: Any User Can Swap on Allowlisted Pools When Router Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is the pool's `msg.sender`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address — a necessary and reasonable step to enable router-based swaps for allowlisted users — any unprivileged user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender` at the time `swap` was called.

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- this is the router when called via router
    recipient,
    ...
    extensionData
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The pool's `msg.sender` is the router. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an impossible choice for pool admins:

| Admin configuration | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | **Any user** can bypass the allowlist via the router |

If the admin allowlists the router (the natural choice to support the protocol's standard swap path), the allowlist is completely defeated for all router-mediated swaps. The extension has no way to distinguish a router call from an allowlisted user versus a router call from a non-allowlisted user, because the router does not forward the originating user's identity to the extension.

The `DepositAllowlistExtension` has the same structural issue for the `MetricOmmPoolLiquidityAdder` path, but the adder gates on `owner` (position owner), which is caller-supplied and checked correctly. The swap path is more severe because the router is the standard entry point for all swaps. [4](#0-3) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific parties (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) is fully bypassed once the router is allowlisted. Any unprivileged user can call `router.exactInputSingle` or `router.exactInput` targeting the curated pool and execute swaps at the oracle-derived price, draining LP value to unauthorized parties. This is a direct loss of LP principal and a complete failure of the pool's curation invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's standard swap entry point. Pool admins who deploy curated pools and want their allowlisted users to be able to use the router will naturally allowlist the router address. This is not a malicious or unusual configuration — it is the expected operational setup. Once the router is allowlisted, the bypass is reachable by any unprivileged user with no special access or capital requirements beyond the swap itself.

---

### Recommendation

The extension must gate on the originating user's identity, not the immediate pool caller. Two approaches:

1. **Router forwards user identity in `extensionData`**: The router encodes `msg.sender` (the originating user) into the `extensionData` it passes to the pool. The extension decodes and checks this address. The extension must also verify that the `extensionData` was produced by a trusted router (e.g., by checking `msg.sender` against a factory-registered router set).

2. **Pool exposes originating sender via transient storage**: The pool stores the originating `msg.sender` in a transient slot before calling extensions, and extensions read it directly. This avoids relying on caller-supplied `extensionData`.

Either approach must be resistant to spoofing: a malicious caller must not be able to forge an allowlisted identity in `extensionData`.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists alice and the router (to let alice use the router)
swapAllowlist.setAllowedToSwap(address(pool), alice, true);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) routes through the router
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);

// Router calls pool.swap with msg.sender = router
// Extension checks allowedSwapper[pool][router] → true
// Bob's swap succeeds despite not being allowlisted
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bob,
        deadline: block.timestamp + 1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Bob receives token1 from the curated pool without being allowlisted
vm.stopPrank();
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which checks `allowedSwapper[msg.sender][sender]` where `sender` is the router address, not the originating user. [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
