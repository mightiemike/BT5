### Title
SwapAllowlistExtension gates on router address instead of actual user — allowlist bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the router is added to the allowlist (the only way to enable router-mediated swaps on a curated pool), the per-user allowlist is completely bypassed: any unprivileged user can swap on a restricted pool by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value received from the pool — i.e., the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original user's address: [4](#0-3) 

The router stores the actual user only in its own transient callback context (for payment settlement), never in the pool call arguments. The pool therefore always sees `msg.sender = router` as `sender`.

This creates two broken states:

**State A — Router not allowlisted:** Pool admin adds individual user addresses to `allowedSwapper`. Every router-mediated swap reverts with `NotAllowedToSwap` because the router address is not in the allowlist. Legitimate, allowlisted users cannot use the supported periphery path.

**State B — Router allowlisted (bypass):** To fix State A, the pool admin adds the router to `allowedSwapper`. Now `allowedSwapper[pool][router] = true`, and the check passes for every call that arrives through the router — regardless of who the actual user is. Any unprivileged address can call `router.exactInputSingle(...)` and swap on the curated pool.

The analog to the FlasherFTM/Cream bug is exact: in that report, the `initiator` value passed to `onFlashLoan` could be spoofed because Cream allowed an arbitrary initiator. Here, the `sender` value passed to `beforeSwap` is structurally wrong when the router is the intermediary — the extension receives the router's address instead of the actual economic actor, undermining the entire allowlist.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional market makers, or protocol-owned addresses) is fully open to any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle-anchored prices, extracting value from LP positions or violating the curation policy the pool admin intended to enforce. This is a direct loss of the allowlist protection with fund-impacting consequences for LPs on curated pools.

---

### Likelihood Explanation

The router is the primary supported swap interface for end users. Any pool that wants to support router-mediated swaps while also enforcing a per-user allowlist is forced into the bypass configuration. The pool admin has no in-protocol mechanism to pass the original user's identity through the router to the extension. The bypass is reachable by any unprivileged address with a single public call to `exactInputSingle`.

---

### Recommendation

The pool should pass the original user's identity to extensions rather than `msg.sender`. Two complementary fixes:

1. **Router-side:** Add an optional `swapper` field to swap params that the router populates with `msg.sender` and forwards as part of `callbackData` or a dedicated argument. The pool can then pass this verified identity to extensions.

2. **Extension-side (short-term):** `SwapAllowlistExtension` should check both `sender` (the immediate caller) and, if `sender` is a known trusted router, also validate the actual user identity extracted from `callbackData`. This mirrors the FlasherFTM hotfix pattern of storing and validating a trusted artifact before the external call.

3. **Pool-side (preferred):** Add a `swapper` parameter to `pool.swap()` that the pool verifies equals `msg.sender` when called directly, but can be set by a trusted router. Extensions then receive the verified original user.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed for router-mediated swaps

Attack:
  - attacker (not alice, not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: attacker, ...})
  - Router calls pool.swap(attacker, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes for attacker despite attacker not being in the allowlist

Result:
  - attacker swaps on a curated pool that should have rejected them
  - allowlist policy is completely bypassed via the supported periphery path
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
