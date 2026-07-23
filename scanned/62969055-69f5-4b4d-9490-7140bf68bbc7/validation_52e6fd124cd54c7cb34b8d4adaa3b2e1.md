### Title
`SwapAllowlistExtension` gates the router's address instead of the actual end-user, allowing any user to bypass the swap allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` inside `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router is added to the allowlist (the natural production setup), the allowlist is completely bypassed for all users.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`. It passes no information about the original caller: [4](#0-3) 

The same is true for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a two-sided failure:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user bypasses the allowlist by routing through the router |
| Router **is not** allowlisted | Every individually-allowlisted user is blocked from using the router |

There is no configuration that simultaneously allows legitimate users to use the router **and** enforces the per-user allowlist.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., whitelisted market makers, KYC'd participants, or protocol-controlled accounts) can be freely traded against by any unprivileged user via `MetricOmmSimpleRouter`. The allowlist guard — the only access-control mechanism on the swap path — is silently bypassed. Unauthorized swaps drain LP assets at oracle-derived prices, constituting a direct loss of LP principal and breaking the core pool access-control invariant.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who discovers the allowlist restriction on a direct pool call will naturally try the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. The router is also the expected path for most integrations, making it highly likely that pool admins will allowlist it, which is precisely the condition that opens the bypass to all users.

---

### Recommendation

The pool should forward the original end-user identity through the extension call chain. Two options:

1. **Preferred:** Add an `originalSender` field to the `beforeSwap` hook signature and have the router populate it (e.g., via `extensionData` or a dedicated parameter). The extension checks `originalSender` instead of `sender`.

2. **Simpler short-term:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension level by reverting when `sender` is a known router address — though this is fragile and does not scale.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  5. Router calls pool.swap(...) — pool sees msg.sender = router
  6. Pool calls extension.beforeSwap(router, ...)
  7. Extension checks allowedSwapper[pool][router] == true  → passes
  8. Bob's swap executes, draining LP assets at oracle price

Result:
  Bob, an unprivileged user, successfully swaps on a pool that was supposed to restrict
  trading to alice only. The allowlist guard is completely bypassed.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
