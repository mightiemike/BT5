### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the router contract, not the originating user. This creates a wrong-actor binding: the allowlist gates the router's address rather than the real swapper's address.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the pool's caller: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the router contract. The pool passes the router address as `sender` to `_beforeSwap`. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The actual originating user's allowlist status is never consulted.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

Two fund-impacting consequences follow directly:

**1. Allowlisted users cannot swap through the router.**
A pool configured with `SwapAllowlistExtension` and a curated set of allowlisted users will reject every router-mediated swap because the router address is not in the allowlist. This breaks the core swap flow for the intended user population.

**2. Any user can bypass the allowlist once the router is allowlisted.**
The natural operator fix for consequence #1 is to add the router to the allowlist. Doing so makes `allowedSwapper[pool][router] = true`, which causes the extension to pass for every swap routed through the router — regardless of who the originating user is. A non-allowlisted user (e.g., a sanctioned address, an unverified counterparty) can bypass the curation gate entirely by calling `exactInputSingle` on the public router.

The allowlist is the sole access-control mechanism for curated pools. Its bypass is a direct policy failure with fund-level consequences: the pool receives input tokens from and delivers output tokens to actors the pool admin explicitly excluded.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Pool admins who configure an allowlist will encounter the broken-router problem immediately when their allowlisted users try to use the router. The natural remediation — allowlisting the router — is the exact step that opens the bypass. No special knowledge or privileged access is required; any public user can call `MetricOmmSimpleRouter.exactInputSingle` with any pool address.

---

### Recommendation

The extension must gate the originating user, not the immediate pool caller. Two approaches:

1. **Pass the original user through the router.** The router already knows `msg.sender` (the real user). It could pass it as a separate field in `extensionData`, and the extension could decode and check it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` only when the caller is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, fall back to checking an address decoded from `extensionData`.

3. **Require direct pool calls for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension level by reverting when `sender` is a known router address.

The cleanest fix is option 1: the router passes the originating `msg.sender` inside `extensionData`, and the extension decodes and checks that address when the immediate `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin does NOT allowlist bob

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
  - Swap succeeds ← bob bypasses the allowlist
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37: `allowedSwapper[msg.sender][sender]` where `sender` is the pool's immediate caller (the router), not the originating user. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
