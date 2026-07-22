### Title
SwapAllowlistExtension Checks Router Identity Instead of Actual Swapper, Allowing Any User to Bypass the Configured Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist status rather than the actual user's. If the pool admin allowlists the router (required for any router-based swaps to work for legitimate users), every unpermissioned user can bypass the configured allowlist by routing through the router.

---

### Finding Description

**Configured guard**: `SwapAllowlistExtension` is a `beforeSwap` hook that gates swap access per pool. The admin sets `allowedSwapper[pool][address]` for each permitted swapper.

**How the pool passes `sender` to the extension**: In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that value as the first argument to every configured extension: [2](#0-1) 

**What the extension checks**: `SwapAllowlistExtension.beforeSwap` receives that forwarded `sender` and checks `allowedSwapper[msg.sender][sender]` (where `msg.sender` is the pool): [3](#0-2) 

**Where the identity breaks**: `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The `msg.sender` of that call is the **router**, not the end user: [4](#0-3) 

The same substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` — in every case the router is `msg.sender` of `pool.swap()`. [5](#0-4) 

So the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

**The dilemma this creates for pool admins**: To allow any legitimate user to swap through the router, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once that entry exists, the check passes for **every** caller of the router — including users who were never individually permitted. The admin cannot simultaneously enable router-based swaps for allowlisted users and block non-allowlisted users from using the same router path.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted strategies) is rendered ineffective the moment the router is added to the allowlist. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap against the pool, bypassing the intended access control entirely. This constitutes a broken core pool functionality — the allowlist guard the pool was configured with fails to enforce its policy on the standard public swap path.

---

### Likelihood Explanation

The router is the canonical periphery entry point for swaps. Pool admins who want their allowlisted users to be able to use the router (rather than calling the pool directly) must add the router to the allowlist. This is the expected operational pattern. The bypass is therefore reachable on any curated pool that supports router-based access, which is the common case.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary contract. Two approaches:

1. **Pass the original initiator through the router**: Have the router forward `msg.sender` (the end user) as a separate field in `extensionData`, and have the extension decode and check that address. This requires a coordinated change between the router and the extension.

2. **Check `sender` in the extension against a router-aware identity**: The pool could expose the original initiator via transient storage (already used for callback context), and the extension could read it. This keeps the extension self-contained.

The simplest correct fix is option 1: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when the caller is a known router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Bob's swap executes successfully against the curated pool, bypassing the configured allowlist.

The guard configured by the pool admin — `allowedSwapper[pool][bob] = false` — is never consulted on the router path.

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
