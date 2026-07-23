### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted rather than the **actual user**. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on-chain.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router address**, so `sender` forwarded to the extension is the router, not the originating user. The extension evaluates `allowedSwapper[pool][router]` — a single entry that covers every user who routes through the router — instead of `allowedSwapper[pool][user]`.

The same identity substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To allow those addresses to use the standard router, the admin must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, **every** address on-chain can bypass the allowlist by calling any `exact*` function on the router. The allowlist is completely ineffective for router-mediated swaps. Users who are explicitly denied direct pool access can trade freely through the router, draining LP value or executing trades the pool admin intended to block.

Severity: **High** — direct policy bypass on curated pools; any unprivileged user is the trigger; no special setup beyond the pool admin's own necessary configuration.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed swap interface. Any pool admin who wants allowlisted users to be able to swap via the router must allowlist the router address. This is the expected operational path. The bypass is therefore reachable in every realistic curated-pool deployment that supports router access.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. The pool admin must also configure the extension to trust the router as a forwarder.

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens. If the pool's design intent is to gate who receives value, checking `recipient` (the second argument to `beforeSwap`) is already the correct identity in router flows, since the router passes the user-supplied `params.recipient` directly.

The simplest safe fix matching the existing interface: require the router to forward the real caller in `extensionData` and update `SwapAllowlistExtension.beforeSwap` to decode and verify it when `sender` is a known trusted forwarder.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router path
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
  2. Router calls pool.swap(recipient, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes — attacker bypassed the allowlist entirely.

Direct call (blocked):
  1. attacker calls pool.swap(...) directly
  2. Pool calls _beforeSwap(sender=attacker, ...)
  3. SwapAllowlistExtension checks allowedSwapper[pool][attacker] → false → revert NotAllowedToSwap
```

The attacker pays no extra cost beyond normal swap gas. The bypass is deterministic and repeatable every block. [6](#0-5) [7](#0-6) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-113)
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
