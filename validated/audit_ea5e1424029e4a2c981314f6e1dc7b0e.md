### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Checked Instead of Actual Swapper - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool always passes `msg.sender` of the `pool.swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the **router's address** rather than the actual end-user. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every user—including non-allowlisted ones—can bypass the guard entirely by calling the router.

---

### Finding Description

**Step 1 – Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the `_beforeSwap` call hardcodes `msg.sender` as the `sender` argument: [1](#0-0) 

**Step 2 – Extension checks that `sender` against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses the `sender` parameter (not `msg.sender`, which is the pool) to look up the allowlist: [2](#0-1) 

**Step 3 – Router calls `pool.swap()` directly, making itself `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with no forwarding of the original caller: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The invariant break:** The pool admin configures `SwapAllowlistExtension` to restrict swaps to a specific set of addresses. To allow any router-mediated swap at all, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router, regardless of who the actual end-user is. A non-allowlisted user simply calls `exactInputSingle` on the router and the guard is silently satisfied. [5](#0-4) 

---

### Impact Explanation

**Direct loss / broken core functionality.** The swap allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, institutional, or whitelist-only markets). A complete bypass means:

- Any unprivileged user can execute swaps in a pool that is supposed to be closed to them.
- The pool's token reserves are exposed to unrestricted arbitrage or directional flow from non-allowlisted actors, directly harming LP principal.
- Protocol-level invariant "only allowlisted addresses may swap" is violated on every router-mediated call.

This matches the Sherlock High threshold: broken core pool functionality causing loss of funds or unusable access-control flows.

---

### Likelihood Explanation

**Medium.** The precondition is that the pool admin allowlists the router. This is the natural, expected configuration for any pool that wants to support the standard periphery router for its allowlisted users—the admin has no other way to enable router-mediated swaps. The router is a public, permissionless contract, so once it is allowlisted, the bypass is trivially reachable by any address with no special privileges.

---

### Recommendation

The pool must forward the original caller's identity through the extension interface. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should pass the original `msg.sender` as an `extensionData` field so extensions can decode it. This requires a convention between the router and extensions.

2. **Extension-side (preferred):** `SwapAllowlistExtension.beforeSwap` should accept an optional `extensionData` payload that carries the true end-user address when the direct caller is a known router/aggregator, and fall back to `sender` only when called directly. Alternatively, the pool interface could be extended to carry a separate `originator` field.

3. **Immediate mitigation:** Document that pools using `SwapAllowlistExtension` must **not** allowlist the router address, and that router-mediated swaps are incompatible with this extension until the identity-forwarding mechanism is implemented.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router swap for legitimate allowlisted users).
  - Alice (address NOT in allowlist) calls:
      router.exactInputSingle({pool: pool, recipient: alice, ...})

Execution trace:
  router.exactInputSingle()          [msg.sender = Alice]
    → pool.swap(recipient=Alice, ...)  [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (guard passes)
      → swap executes, Alice receives tokens

Result:
  Alice, a non-allowlisted address, successfully swaps in a restricted pool.
  The allowlist guard is completely bypassed.
```

The root cause is in `MetricOmmPool.swap` passing `msg.sender` (the router) as `sender` to the extension, while `SwapAllowlistExtension.beforeSwap` treats that argument as the identity to gate. [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
