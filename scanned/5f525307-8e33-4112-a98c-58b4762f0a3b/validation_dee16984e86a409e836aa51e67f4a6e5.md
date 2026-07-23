### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass Per-User Allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the per-user allowlist by routing through the same public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value and dispatches it to each extension in order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The router does not forward the original end-user's identity anywhere in the call to `pool.swap()`. The pool only ever sees the router's address as `sender`.

This creates an inescapable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot swap through the router at all (broken UX) |
| Yes | **Every** user, allowlisted or not, can bypass the per-user gate by routing through the router |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users, because the extension has no visibility into the end user's identity.

---

### Impact Explanation

Any unprivileged address that is explicitly excluded from the allowlist can bypass the `SwapAllowlistExtension` guard by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) on a pool where the router is allowlisted. The extension's core invariant — that only approved addresses may swap — is silently violated. Downstream consequences include unauthorized price impact on LP positions, MEV extraction against restricted pools, and regulatory/compliance violations for pools that use the allowlist to enforce participant identity requirements.

---

### Likelihood Explanation

The pool admin must have allowlisted the router for the bypass to be active, but this is the expected operational configuration: without it, no allowlisted user can use the router either. The router is a public, permissionless contract. Once the router is allowlisted, the bypass is available to any address with no additional preconditions.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Router forwards user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router; otherwise reject or require explicit per-router-user approval**: The extension maintains a registry of trusted routers and, when `sender` is a router, falls back to checking the `recipient` or a user identity embedded in `extensionData`.

The simplest safe default is to remove the router from the allowlist and require end users to call `pool.swap()` directly, accepting the UX trade-off until a proper identity-forwarding mechanism is in place.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  pool admin calls setAllowedToSwap(pool, userA, true)       // allowlist userA
  pool admin calls setAllowedToSwap(pool, router, true)      // allowlist router so userA can use it

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

<cite repo="Oyahkilomeikhide/2026-07-metric-dev-oyakhil-main--021" path

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
