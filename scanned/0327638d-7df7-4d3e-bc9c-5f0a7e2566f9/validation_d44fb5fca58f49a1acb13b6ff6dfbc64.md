### Title
`SwapAllowlistExtension` Checks Router Identity Instead of User Identity, Allowing Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the user. A pool admin who allowlists the router to enable router-mediated swaps for their curated pool inadvertently opens the pool to every user, completely bypassing the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that value as the first positional argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists a set of approved users.
2. To let those approved users trade conveniently, the admin also calls `setAllowedToSwap(pool, router, true)`.
3. Any unapproved user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The pool sees `msg.sender = router`, the extension sees `sender = router`, finds `allowedSwapper[pool][router] == true`, and passes — the unapproved user swaps freely.

Even without step 2, approved users who try to use the router are blocked (the router is not on their individual allowlist), so the extension is broken in both directions.

---

### Impact Explanation

A curated pool whose entire purpose is to restrict trading to a vetted set of counterparties becomes open to any public user the moment the router is allowlisted. Unapproved users can drain LP value through adverse selection or execute trades the pool admin explicitly prohibited. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point. Pool admins who want approved users to trade conveniently will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any public user can trigger it with a single router call. The condition is therefore highly likely to be met on any production curated pool.

---

### Recommendation

Gate by the economic actor, not the immediate caller. Two options:

**Option A — Check `sender` only when the caller is not a known router; otherwise check the payer stored in transient storage.** This requires the extension to be router-aware.

**Option B (simpler) — Change `SwapAllowlistExtension.beforeSwap()` to check `msg.sender` (the pool's caller, i.e., the router) AND require the router to forward the real user identity in `extensionData`, then verify that identity against the allowlist.**

**Option C (recommended) — Document that `sender` is the immediate `pool.swap()` caller and require pool admins to allowlist the router only when they intend to open the pool to all users; provide a separate `UserSwapAllowlistExtension` that reads the real user from `extensionData` forwarded by the router.**

At minimum, the NatSpec on `SwapAllowlistExtension` must warn that allowlisting the router grants access to all router users, and the router's `exactInput*` functions should forward the originating user in `extensionData` so extensions can verify it.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1)
  admin calls setAllowedToSwap(pool, alice, true)      // alice is approved
  admin calls setAllowedToSwap(pool, router, true)     // enable router for alice

Attack:
  bob (not approved) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)   [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, bob receives tokens

Result: bob bypasses the allowlist and trades on a curated pool.
``` [5](#0-4) [6](#0-5)

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
