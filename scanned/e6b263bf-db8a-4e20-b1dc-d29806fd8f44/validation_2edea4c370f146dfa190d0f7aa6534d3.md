### Title
SwapAllowlistExtension Checks Router Address Instead of Real User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted — not the actual end user. A pool admin who adds the router to the allowlist (the only way to support router-mediated swaps on a curated pool) inadvertently opens the gate to every user on the internet, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** the extension never sees the real end-user address. It sees only the router's address. A pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Every router-mediated swap reverts — allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the internet can bypass the allowlist by routing through the router |

There is no configuration that simultaneously supports the router and enforces per-user access control.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted — the only way to let legitimate users trade through the standard periphery — the allowlist is completely bypassed. Any unprivileged address can call `router.exactInputSingle(pool, ...)` and execute a swap on the curated pool. This is a direct loss of access-control integrity and, depending on the pool's purpose, can result in unauthorized trading, unauthorized extraction of LP value, or violation of regulatory/compliance constraints the pool was designed to enforce.

**Severity: High** — the allowlist is a core security primitive; its complete bypass via a public supported entrypoint is a broken invariant with direct fund-impacting consequences.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Any pool admin who wants to support normal user interaction through the router must allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a single call to `router.exactInputSingle` from any EOA is sufficient.

---

### Recommendation

The extension must check the **real end-user** identity, not the intermediary. Two sound approaches:

1. **Pass `tx.origin` as an additional argument** — the pool could forward `tx.origin` alongside `msg.sender` so extensions can gate on the originating EOA. This is simple but breaks contract-to-contract composability.

2. **Router forwards the real caller** — `MetricOmmSimpleRouter` should pass `msg.sender` (the real user) as a verified field inside `extensionData` (signed or authenticated via transient storage), and the extension should decode and check that field instead of `sender`. The pool already uses transient storage for callback context, so the same pattern applies here.

3. **Allowlist the router with a per-user sub-check** — deploy a wrapper extension that, when `sender == router`, decodes the real user from `extensionData` and checks that address against the allowlist. This keeps the core extension unchanged but requires router cooperation.

Option 2 is the cleanest: it preserves the extension interface and lets the router authenticate the real user without breaking the pool's trust model.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only intended swapper)
  - allowedSwapper[pool][router] = true  (admin adds router so alice can use it)

Attack:
  1. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. router calls pool.swap(bob, ...) — msg.sender to pool is router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes for bob despite bob never being allowlisted

Invariant broken:
  allowedSwapper[pool][bob] == false, yet bob successfully swaps on the curated pool.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
