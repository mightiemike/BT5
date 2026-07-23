Let me look at the pool's `swap` function to confirm what it passes as `sender` to the extension's `beforeSwap`.

The call chain is fully traceable and the vulnerability is real. Here is the complete analysis:

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Any User to Bypass Swap Allowlist via Allowlisted Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call — the router — not the originating user. If the router is allowlisted, any user can route through it to bypass the per-pool swap allowlist entirely.

### Finding Description

The call chain is:

**Step 1.** A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle`. The router then calls `pool.swap(...)` — so `msg.sender` inside the pool is the **router**, not the user. [1](#0-0) 

**Step 2.** `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the **router address** as `sender`. [2](#0-1) 

**Step 3.** `ExtensionCalling._beforeSwap` forwards that `sender` (the router) to `extension.beforeSwap(sender, ...)`. [3](#0-2) 

**Step 4.** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router. Since the router is allowlisted, the check passes — the originating user is **never consulted**. [4](#0-3) 

The `IMetricOmmExtensions.beforeSwap` interface does receive both `sender` and `recipient`, but there is no `originator`/`user` field — the pool has no mechanism to pass the true end-user through the router layer. [5](#0-4) 

### Impact Explanation

Pool admins deploy `SwapAllowlistExtension` specifically to restrict which addresses may swap — this is the extension's sole purpose. A pool admin who allowlists the router (a natural and expected configuration to let users use the official router) inadvertently grants every user on-chain the ability to swap, defeating the allowlist entirely. The swap executes at live oracle prices with real token transfers; the non-allowlisted user receives real output tokens. Pool curation is broken and cannot be enforced while the router is allowlisted.

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call `exactInputSingle`. The only precondition is that the pool admin has allowlisted the router address — a configuration that is both natural and expected for any pool that wants to support the official router. No privileged access, no special token behavior, and no malicious pool setup is required.

### Recommendation

The pool's `swap` interface should propagate the true originator. One approach: add an `originator` field to the `beforeSwap` extension call (set to `msg.sender` at the router entry point and forwarded through `callbackData` or a dedicated parameter). Alternatively, `SwapAllowlistExtension` should check both `sender` (the router) and require the router to attest the originating user via `extensionData`, with the router populating that field as `msg.sender` before calling the pool.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
3. Pool admin does NOT call setAllowedToSwap(pool, user, true)
4. user calls router.exactInputSingle({pool: pool, ...})
5. router calls pool.swap(recipient, ...) — msg.sender in pool = router
6. pool calls extension.beforeSwap(router, ...) — sender = router
7. Extension checks allowedSwapper[pool][router] == true → passes
8. Swap executes; user receives output tokens.
9. USER_IS_ALLOWLISTED_SWAPPER invariant is violated: non-allowlisted user
   successfully swapped through an allowlisted router.
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
