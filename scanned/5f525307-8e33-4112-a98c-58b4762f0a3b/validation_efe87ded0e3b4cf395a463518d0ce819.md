### Title
SwapAllowlistExtension Checks Router Address Instead of End-User — Any User Bypasses Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every user on the internet, completely defeating the per-user allowlist.

---

### Finding Description

**Execution path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...) [msg.sender = router]
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]  ← wrong identity
```

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension:** [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that router address as `sender`:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the router:** [3](#0-2) 

**Step 4 — The router calls `pool.swap()` directly, making itself `msg.sender` to the pool:** [4](#0-3) 

The allowlist is keyed on `(pool, sender)`. When the router is the sender, the check is `allowedSwapper[pool][router]`. If the pool admin allowlists the router (a natural action to enable router-mediated swaps for allowlisted users), the check passes for **every** user who calls the router, regardless of whether that user is individually allowlisted.

This is structurally identical to the IronBank `msg.value` bug: a context change (callback / router intermediary) silently substitutes the wrong value (`msg.value = 0` / `sender = router`) into a guard that was designed to inspect the original actor.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers). To allow those users to also swap through the official router, the admin calls `setAllowedToSwap(pool, router, true)`. From that moment, any unprivileged user can call `router.exactInputSingle(...)` and the extension sees `sender = router`, which is allowlisted, so the swap executes. The allowlist is completely bypassed. Unauthorized users can drain pool liquidity at oracle-derived prices, causing direct loss of LP principal.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router, which is the natural and expected configuration for any pool that wants to support both direct and router-mediated swaps for its allowlisted users. The `MetricOmmSimpleRouter` is a public, permissionless contract. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no front-running, and no complex setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original end-user identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Router passes original user in `extensionData`**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the user; however this breaks for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router appends a verified-caller field to `extensionData`, and the extension decodes it when the immediate `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  admin calls setAllowedToSwap(pool, router, true)  // enable router for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  pool.swap() receives msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  Swap executes for bob — allowlist bypassed
```

Bob receives pool output tokens without ever being individually allowlisted. The pool admin's intent to restrict swaps to KYC'd users is silently defeated by the router intermediary substituting its own address as `sender`.

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
