### Title
`SwapAllowlistExtension` gates on the router address instead of the end-user, allowing any unprivileged caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist check therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the router is allowlisted (the natural configuration for any pool that wants to support router-mediated swaps), every user on-chain can bypass the individual allowlist restriction by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract: [4](#0-3) 

The allowlist lookup therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Two mutually exclusive failure modes result:

1. **Bypass (primary impact):** The pool admin allowlists the router so that legitimate router-mediated swaps work. Every user on-chain can now call `exactInputSingle` and pass the allowlist check, because the check is satisfied by the router's allowlist entry, not the user's.

2. **DoS (secondary impact):** The pool admin does not allowlist the router. Every individually allowlisted user who tries to swap through the router is blocked, even though they are explicitly permitted.

The `exactInput` multi-hop path has the same flaw for every hop: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-controlled addresses). When the guard is bypassed, any unprivileged user can trade against the pool's LP liquidity. LPs deposited into a restricted pool under the assumption that only vetted counterparties would trade against them; exposure to unrestricted counterparties can cause direct LP principal loss through adversarial order flow (e.g., exploiting a stale oracle window that the allowlist was meant to prevent from being reached by arbitrary actors). This matches the "allowlist or oracle guard silently fails open on a production pool" criterion.

---

### Likelihood Explanation

The bypass requires only that the router be allowlisted for the pool, which is the natural and expected configuration for any pool that wants to support standard user-facing swap flows. A pool admin who deploys `SwapAllowlistExtension` and also wants users to be able to use the router must allowlist the router, at which point the individual allowlist is entirely ineffective. No privileged action, malicious setup, or non-standard token is required; any user with a wallet can call `MetricOmmSimpleRouter.exactInputSingle`.

---

### Recommendation

The `beforeSwap` hook must gate on the actual end-user identity, not the immediate pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require a signed or encoded user identity for router calls:** The extension can detect that `sender` is a known router and require the real user to be embedded in `extensionData` with a verifiable binding.

The simplest correct fix is to have the router always embed the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a recognized router address.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice; Bob is not allowlisted.
4. Bob calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender = router`.
6. `_beforeSwap(router, ...)` is dispatched to `SwapAllowlistExtension.beforeSwap(router, ...)`.
7. The check evaluates `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds.
8. Bob, who was never allowlisted, has traded against the restricted pool's LP liquidity. [6](#0-5) [7](#0-6)

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
