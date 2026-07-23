The vulnerability is real and confirmed. Here is the complete trace and finding.

---

### Title
Router-Mediated Swap Bypasses `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool::swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. Any unprivileged user can therefore bypass a per-user allowlist on a curated pool simply by routing through the router, provided the router address is itself allowlisted.

---

### Finding Description

**Call chain — direct swap (intended):**

```
user → pool.swap(...)
         msg.sender = user
         _beforeSwap(sender=user, ...)
         → SwapAllowlistExtension.beforeSwap(sender=user)
         → allowedSwapper[pool][user]  ✓ correct actor
```

**Call chain — router-mediated swap (bypassed):**

```
user → router.exactInputSingle(params)
         → pool.swap(params.recipient, ...)   // msg.sender = router
              _beforeSwap(sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router)
              → allowedSwapper[pool][router]  ✗ wrong actor
```

In `MetricOmmPool::swap`, the `sender` forwarded to the hook is always `msg.sender`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` passes that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

In `MetricOmmSimpleRouter::exactInputSingle`, the router calls `pool.swap` directly — so the pool sees `msg.sender = router`, not the original user: [4](#0-3) 

The same applies to `exactOutputSingle`: [5](#0-4) 

And to intermediate hops in `exactInput` (where `msg.sender` is `address(this)` = router for all hops after the first): [6](#0-5) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses. To allow those users to also trade via the router, the admin must allowlist the router address. Once the router is allowlisted, **any** unprivileged user can call `exactInputSingle` / `exactOutputSingle` / `exactInput` / `exactOutput` and the hook will pass — because the hook sees `sender = router`, which is allowlisted. The per-user allowlist is completely defeated.

Alternatively, if the admin does **not** allowlist the router, then even legitimately allowlisted users cannot use the router at all — the hook rejects them because `allowedSwapper[pool][router] = false`. The allowlist is broken in both directions.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical public swap interface for the protocol.
- Any pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router, which immediately opens the gate to all users.
- No special privileges, no malicious pool setup, and no non-standard token behavior are required. Any user with tokens can exploit this.

---

### Recommendation

Pass the **originating user** through the extension data or a dedicated field, and have the hook verify that identity. One approach: the pool could expose a `swapWithSender` variant that accepts an explicit `swapper` address verified against `msg.sender` or a trusted router registry. Alternatively, the router should pass the real user address in `extensionData`, and the extension should decode and check it — but this requires the extension to trust the router, which itself needs a registry check.

The cleanest fix is for the pool to record the original `tx.origin` or for the router to pass the real payer address in `extensionData` and for the extension to verify the router is trusted before accepting that override.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (to allow router-mediated swaps for allowlisted users) and `setAllowedToSwap(pool, alice, true)`.
3. `charlie` (not allowlisted) calls `MetricOmmSimpleRouter::exactInputSingle` targeting the pool.
4. The pool's `swap` is entered with `msg.sender = router`.
5. `beforeSwap` checks `allowedSwapper[pool][router] == true` → passes.
6. `charlie` successfully swaps on a pool that was supposed to block them.

Assert: `allowedSwapper[pool][charlie] == false` yet the swap succeeds — the invariant is broken. [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
