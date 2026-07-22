### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the end user. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate to every user, completely defeating the per-user allowlist.

---

### Finding Description

**Step 1 — How the pool passes `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the first argument to every registered extension: [2](#0-1) 

**Step 2 — What `sender` equals when the router is used.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. At that call site, `msg.sender` of the pool is the **router contract**, not the end user: [3](#0-2) 

The same is true for `exactInput` (all hops) and `exactOutputSingle`: [4](#0-3) 

**Step 3 — What the allowlist actually checks.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., the router address: [5](#0-4) 

**Step 4 — The bypass.**

For allowlisted users to be able to swap through the router at all, the pool admin must add the router to `allowedSwapper[pool][router] = true`. The moment that entry exists, the check `allowedSwapper[pool][router]` returns `true` for **every** caller who routes through the router — including users who were never individually allowlisted. The per-user granularity is lost entirely.

There is no mechanism in the current design to simultaneously (a) permit allowlisted users to swap via the router and (b) block non-allowlisted users from doing the same.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific participants (e.g., KYC-verified counterparties, institutional market makers, or whitelisted addresses only) cannot enforce that restriction for router-mediated swaps. Any unprivileged user can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle`). This breaks the core access-control invariant of the extension and allows unauthorized parties to trade against the pool's LPs, potentially extracting value if the oracle price is even slightly stale or if the pool carries favorable inventory.

**Impact class:** Broken core pool functionality — the allowlist guard fails open for the primary public swap path.

---

### Likelihood Explanation

High. The router is the standard user-facing entry point. Any pool admin who deploys a `SwapAllowlistExtension` and also wants users to be able to use the router must allowlist the router address. This is the expected operational configuration, and it is the configuration that triggers the bypass. No special attacker capability is required beyond calling the public router.

---

### Recommendation

The extension must gate the **original end-user**, not the intermediary router. Two viable approaches:

1. **Pass the real caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks that address. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Check `sender` only when `sender` is not a known router; otherwise decode the real caller from `extensionData`:** The extension maintains a registry of trusted routers and, for those callers, reads the actual user from a standardized field in `extensionData`.

3. **Preferred — enforce at the pool level:** The pool could expose the original `tx.origin` or a signed-permit pattern so the extension always sees the economic actor. However, `tx.origin` has its own risks.

At minimum, the documentation and admin tooling must warn that allowlisting the router is equivalent to `allowAllSwappers = true`, and the extension interface should make this impossible to configure accidentally.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes — bob has bypassed the allowlist

Verification:
  - Direct call: bob calls pool.swap(...) directly
    → allowedSwapper[pool][bob] == false → revert NotAllowedToSwap  ✓ (blocked)
  - Router call: bob calls router.exactInputSingle(...)
    → allowedSwapper[pool][router] == true → swap succeeds  ✗ (bypass)
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
