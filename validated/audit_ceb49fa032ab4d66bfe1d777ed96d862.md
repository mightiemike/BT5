Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's immediate `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. This causes the extension to evaluate `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, breaking the allowlist's intended access control in two directions: allowlisted users are blocked through the router, and once the router is allowlisted as a workaround, any user can bypass the allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the call dispatched to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same applies to `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181). In every case, the router is the pool's `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` — the originating user's allowlist status is never consulted. There is no existing guard that recovers the true caller from `extensionData` or any other channel. [5](#0-4) 

## Impact Explanation
Two fund-impacting consequences follow directly:

**1. Broken core swap flow for allowlisted users.** A pool configured with `SwapAllowlistExtension` and a curated allowlist will reject every router-mediated swap because the router address is not in the allowlist. Allowlisted users cannot use the standard periphery entry point.

**2. Complete allowlist bypass once the router is allowlisted.** The natural operator remediation — adding the router to the allowlist — sets `allowedSwapper[pool][router] = true`, causing the extension to pass for every swap routed through the router regardless of who the originating user is. Any non-allowlisted user (e.g., a sanctioned address) can bypass the curation gate by calling `exactInputSingle` on the public router. The pool receives input tokens from and delivers output tokens to actors the pool admin explicitly excluded. This is a broken core pool functionality / admin-boundary break with direct fund-level consequences.

## Likelihood Explanation
The router is the standard, documented periphery entry point. Pool admins who configure an allowlist will encounter the broken-router problem immediately when their allowlisted users attempt to use the router. The natural remediation is the exact step that opens the bypass. No special knowledge, privileged access, or unusual conditions are required — any public user can call `MetricOmmSimpleRouter.exactInputSingle` with any pool address.

## Recommendation
The extension must gate the originating user, not the immediate pool caller. The cleanest fix: the router encodes the originating `msg.sender` inside `extensionData`, and the extension decodes and checks that address when the immediate `sender` is a recognized router. Concretely:

- `MetricOmmSimpleRouter` prepends `abi.encode(msg.sender)` to `params.extensionData` before passing it to `pool.swap`.
- `SwapAllowlistExtension.beforeSwap` maintains a registry of trusted routers; when `sender` is a trusted router, it decodes the first 32 bytes of `extensionData` as the real user and checks `allowedSwapper[pool][realUser]`.

Alternatively, document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps by reverting when `sender` is a known router address.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin does NOT allowlist bob or the router

Step 1 — Allowlisted user blocked through router:
  - alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) → msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → false
  - Revert: NotAllowedToSwap  ← alice cannot use the router

Step 2 — Operator "fixes" by allowlisting the router:
  - Pool admin calls setAllowedToSwap(pool, router, true)

Step 3 — Non-allowlisted user bypasses the allowlist:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) → msg.sender to pool = router
  - Extension checks allowedSwapper[pool][router] → true
  - Swap succeeds ← bob bypasses the allowlist entirely
```

Root cause: `allowedSwapper[msg.sender][sender]` at line 37 of `SwapAllowlistExtension.sol`, where `sender` is the pool's immediate caller (the router), not the originating user. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
