Based on my review of the actual code in the repository, all cited code paths are confirmed:

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is whoever called `pool.swap()` [2](#0-1) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no end-user identity forwarding, making the router the `msg.sender` seen by the pool [3](#0-2) 

The exploit path is real and the inescapable dilemma for the pool admin is correctly described.

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass Per-User Allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees the router's address as `sender`, not the end user's. If the pool admin allowlists the router so that legitimate users can swap through it, every non-allowlisted user can bypass the per-user gate by routing through the same public router contract.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap` (MetricOmmPool.sol L230–240). `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` (SwapAllowlistExtension.sol L37). `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (MetricOmmSimpleRouter.sol L72–80), making the router the `msg.sender` of that call. The router does not encode or forward the original caller's identity anywhere in the call. As a result, the pool only ever sees the router's address as `sender`. The pool admin faces an inescapable dilemma: if the router is not allowlisted, no allowlisted user can swap through it; if the router is allowlisted (the expected operational configuration), every address — allowlisted or not — can bypass the gate by routing through the public router. No existing guard in the extension or the pool checks the end user's identity when an intermediary router is involved.

## Impact Explanation
Any unprivileged address explicitly excluded from the allowlist can bypass `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle` on a pool where the router is allowlisted. The extension's core invariant — that only approved addresses may swap — is silently violated. This constitutes broken core pool functionality (access control bypass on a configured extension) and an admin-boundary break where an unprivileged path circumvents a pool-level restriction. Downstream consequences include unauthorized price impact on LP positions and regulatory/compliance violations for pools using the allowlist to enforce participant identity requirements.

## Likelihood Explanation
The pool admin must allowlist the router, but this is the expected and necessary operational configuration: without it, no allowlisted user can use the router either. The router (`MetricOmmSimpleRouter`) is a public, permissionless contract. Once the router is allowlisted, the bypass requires no special privileges, no additional preconditions, and is repeatable by any address on every swap.

## Recommendation
The extension must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Router forwards user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address against the allowlist. This requires a convention between the router and the extension.
2. **Trusted-router registry with fallback identity check**: The extension maintains a registry of trusted routers. When `sender` is a known router, the extension falls back to checking a user identity embedded in `extensionData` rather than `sender` itself.

The simplest safe default is to remove the router from the allowlist and require end users to call `pool.swap()` directly, accepting the UX trade-off until a proper identity-forwarding mechanism is in place.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  pool admin calls setAllowedToSwap(pool, userA, true)    // allowlist userA
  pool admin calls setAllowedToSwap(pool, router, true)   // allowlist router so userA can use it

Attack:
  userB (NOT allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: userB,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  router calls pool.swap(recipient=userB, ..., callbackData="", extensionData=...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap receives sender=router
    → checks allowedSwapper[pool][router] → TRUE
    → swap proceeds

Result: userB swaps successfully despite not being on the allowlist.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
