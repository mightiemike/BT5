Looking at the codebase, I need to trace how the `SwapAllowlistExtension` guard checks the swapper identity when swaps are routed through `MetricOmmSimpleRouter`.

### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user for router-mediated swaps, breaking the per-user allowlist guard — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes its own `msg.sender` as `sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making the per-user allowlist inoperable for the standard router path.

---

### Finding Description

**Pool → Extension call chain:**

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` against the allowlist: [3](#0-2) 

**Router call chain:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The actual user's allowlist entry is never consulted.

---

### Impact Explanation

Two concrete consequences follow:

1. **Allowlisted users are blocked from the standard swap path.** A pool admin allowlists `userA`. `userA` calls `exactInputSingle` through the router. The extension sees `sender = router`, finds no entry for the router, and reverts with `NotAllowedToSwap`. `userA` cannot use the router despite being explicitly permitted. To swap at all, `userA` must implement `IMetricOmmSwapCallback` and call the pool directly — an interface not exposed to ordinary EOAs.

2. **Per-user allowlist is bypassed when the router is allowlisted.** If the admin allowlists the router address (a natural step to "enable router-mediated swaps"), every user — including those not individually allowlisted — passes the guard. The per-user curation the allowlist was meant to enforce is entirely defeated. LPs who deposited into a curated pool expecting only approved counterparties are exposed to unrestricted trading, which can cause direct LP principal loss through adversarial or uninstructed flow.

---

### Likelihood Explanation

The router is the canonical, documented swap entrypoint for EOAs. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will immediately encounter this mismatch. The bypass path (allowlisting the router) is a predictable admin response to the first symptom (allowlisted users being blocked), making the full bypass reachable through a single, plausible misconfiguration step.

---

### Recommendation

The pool must forward the original user's identity — not its own `msg.sender` — to the extension. Two options:

**Option A (preferred):** Have the router store the originating user in transient storage (analogous to how it already stores the payer) and expose it via a view function. The pool reads this value during `_beforeSwap` and passes it as `sender` when the caller is a known periphery contract.

**Option B:** Require the router to encode the originating user in `extensionData` and have `SwapAllowlistExtension` decode and verify it, with a signature or trusted-forwarder pattern to prevent spoofing.

The current `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner passed explicitly by the caller) rather than `sender`. The swap extension should adopt an equivalent design where the economically relevant identity is passed explicitly and cannot be shadowed by an intermediary.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, userA, true)

Attack path A — allowlisted user blocked:
  userA calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  router calls pool.swap(recipient, ...) → msg.sender = router
  pool calls _beforeSwap(router, ...)
  extension checks allowedSwapper[pool][router] → false
  → revert NotAllowedToSwap
  userA cannot swap despite being allowlisted.

Attack path B — allowlist bypass:
  admin calls setAllowedToSwap(pool, router, true)   // to fix path A
  userB (not individually allowlisted) calls exactInputSingle(pool, ...)
  router calls pool.swap(...) → msg.sender = router
  extension checks allowedSwapper[pool][router] → true
  → swap proceeds
  userB bypasses the per-user allowlist; LP funds exposed to unauthorized flow.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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
