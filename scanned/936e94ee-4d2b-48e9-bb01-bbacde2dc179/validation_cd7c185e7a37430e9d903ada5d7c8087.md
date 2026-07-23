### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the first argument forwarded by the pool — which is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router to support router-based swaps, every user on the network can bypass the curated allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `allowedSwapper[pool][sender]`.**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the first argument) as the identity to gate: [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, so `msg.sender` in the pool is the router.**

`exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `IMetricOmmPoolActions(pool).swap(...)` from within the router contract: [4](#0-3) 

For multi-hop `exactInput`, every hop is called from the router: [5](#0-4) 

**Result:** The extension always sees `sender = router_address` for every router-mediated swap. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

---

### Impact Explanation

The pool admin faces an impossible choice:

| Admin configuration | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Router NOT allowlisted | ✅ allowed | ❌ blocked | ❌ blocked |
| Router allowlisted | ✅ allowed | ✅ allowed | ✅ **bypassed** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. If the admin allowlists the router to support the standard periphery path, the allowlist is completely neutralized for all router-mediated swaps. Any user can trade in a curated pool — bypassing KYC gates, institutional restrictions, or any other access-control policy the pool admin intended to enforce.

This is a direct policy bypass on curated pools with fund-impacting consequences: unauthorized users can execute swaps, drain liquidity at oracle prices, and interact with pools they were explicitly excluded from.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps.
- A pool admin who deploys `SwapAllowlistExtension` to restrict access will naturally also want users to be able to use the standard router.
- Allowlisting the router is the obvious and expected configuration step to enable router support.
- The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can exploit it by calling `exactInputSingle` on the router.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of the pool. Two approaches:

1. **Pass the original user through the router.** The router could forward the original `msg.sender` as an authenticated `sender` field inside `extensionData`, and the extension could decode and verify it. This requires a trust relationship between the extension and the router.

2. **Check `sender` against the allowlist but also accept the router as a transparent forwarder only when the router itself is not the economic actor.** The router could be a special-cased "transparent" address, and the extension could require the router to embed the real user in `extensionData` with a verifiable signature or factory-registered identity.

3. **Restrict the allowlist to direct pool calls only** (document that router-mediated swaps are not supported for allowlisted pools). This is the safest short-term fix but breaks UX.

The simplest safe fix is to not allowlist the router and require allowlisted users to call `pool.swap()` directly, with clear documentation that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // enable router support

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - Bob trades in a curated pool he was explicitly excluded from
  - The SwapAllowlistExtension provides zero protection for router-mediated swaps
``` [3](#0-2) [1](#0-0) [6](#0-5)

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
