### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Any User Can Swap in Allowlist-Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the **router address**, not the actual end-user. If the router is allowlisted (which is required for any router-mediated swap to succeed), every user — including those not on the allowlist — can bypass the gate by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`. If the router is allowlisted (which the admin must do to enable any router-mediated swap), the check passes for **every caller of the router**, regardless of whether that caller is on the allowlist.

The same pattern applies to `exactOutputSingle`, `exactInput` (all hops), and `exactOutput` (all recursive hops in `_exactOutputIterateCallback`).

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers). To also allow those users to swap through the router, the admin must allowlist the router. Once the router is allowlisted, the gate is effectively open to the entire public: any address can call `router.exactInputSingle()` and the extension will approve the swap because it sees `sender = router`. The allowlist no longer restricts the actual economic actor performing the swap.

This breaks the core access-control invariant of the extension. Unauthorized users can execute swaps in pools that should be restricted, potentially violating compliance requirements, draining liquidity that was reserved for specific counterparties, or interacting with pools whose risk parameters were calibrated for a known set of participants.

### Likelihood Explanation

The scenario is reachable whenever:
1. A pool is deployed with `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER`.
2. The pool admin allowlists the router (a natural operational step to enable router-mediated swaps for legitimate users).

Both conditions are expected in normal protocol operation. No special privileges, flash loans, or oracle manipulation are required. Any EOA can trigger the bypass with a single `exactInputSingle` call.

### Recommendation

The `SwapAllowlistExtension` must gate the **actual end-user**, not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust that the router populates the field honestly — which is acceptable only if the router itself is a trusted, non-upgradeable contract.

2. **Check `sender` against the allowlist only when `sender` is not a known router**: Maintain a registry of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData`.

3. **Require direct pool interaction for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with the router and enforce this at the factory level (e.g., reject pool creation that combines a swap allowlist with a non-zero `BEFORE_SWAP_ORDER` pointing to the extension when a router address is not the only allowlisted entity).

The deposit-side analog (`DepositAllowlistExtension`) does **not** share this flaw because it gates `owner` (the position owner passed explicitly to `addLiquidity`), which the liquidity adder preserves correctly.

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls extension.setAllowedToSwap(pool, alice, true)      // alice is the intended user
  admin calls extension.setAllowedToSwap(pool, router, true)     // enable router-mediated swaps for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives output tokens

Result: bob swaps successfully despite not being on the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
```
