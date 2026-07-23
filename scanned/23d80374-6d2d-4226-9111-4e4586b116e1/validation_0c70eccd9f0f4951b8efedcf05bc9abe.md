### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted — not the actual end user. Any pool that allowlists the router (required for router-based swaps to function at all) inadvertently opens the gate to every user on-chain.

---

### Finding Description

**Identity binding in the pool's swap path:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**The guard that is supposed to enforce the allowlist:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

**What the router actually sends:**

Every public entry point in `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the router itself `msg.sender` to the pool: [4](#0-3) [5](#0-4) 

Therefore, when any user routes through the router, the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]` — the actual caller's address is never consulted.

**The forced dilemma for pool admins:**

A pool that deploys `SwapAllowlistExtension` and wants to support router-based swaps **must** call `setAllowedToSwap(pool, router, true)`. The moment it does, the check `allowedSwapper[pool][router]` passes for every user who routes through the router, regardless of whether that user is individually allowlisted. The allowlist is structurally bypassed for all router-mediated swaps.

---

### Impact Explanation

Any user blocked by the swap allowlist can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) instead of calling the pool directly. The pool's `beforeSwap` hook sees the router's address, which is allowlisted, and permits the swap. The user receives output tokens they were not authorized to receive, and the pool's restricted-access invariant is permanently broken for all router paths.

This is a **broken core pool functionality / admin-boundary break**: the pool admin's explicit access-control configuration is silently nullified by a valid, public periphery contract.

---

### Likelihood Explanation

- The router is a public, permissionless contract — any user can call it.
- Any pool that uses `SwapAllowlistExtension` and supports router-based swaps is affected by construction; no special setup or privileged action is required from the attacker.
- The bypass requires only a standard `exactInputSingle` call with the target pool address.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the end user — not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and verifies it. This requires a trusted encoding convention.
2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists the recipient is often the user; however this is also spoofable. The cleanest fix is a dedicated "originator" field in the extension interface.
3. **Document that the allowlist only gates direct pool callers** and remove the router from any allowlisted pool, forcing users to interact directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap slot.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router support
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is blocked

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=alice, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension evaluates: allowedSwapper[pool][router] == true  → passes
  - alice receives output tokens despite being explicitly excluded from the allowlist

Result:
  - The swap allowlist is fully bypassed for all router-mediated swaps.
  - alice (and any other blocked user) can swap freely via the router.
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
