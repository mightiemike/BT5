### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the first argument the pool passes — which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the original user. If the pool admin allowlists the router (the only way to let allowlisted users trade via the router), every unpermissioned user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the **router address**, so `sender` delivered to the extension is the router, not the original user. The extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][original_user]`.

This creates an inescapable dilemma for any pool admin who deploys a swap-allowlisted pool:

| Admin choice | Consequence |
|---|---|
| Allowlist the router | Every unpermissioned user bypasses the allowlist via the router |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

The same identity mismatch applies to every router entry point (`exactInput`, `exactOutput`, `exactOutputSingle`) and to every intermediate hop in multi-hop paths, because the pool's `msg.sender` is always the router for all hops: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — only specific counterparties are supposed to trade. If the router is allowlisted (the only practical way to support router-based trading), any address can call `router.exactInputSingle` and the extension will pass them through. Unauthorized users can:

- Extract value from pools with favorable oracle-driven pricing intended for specific counterparties.
- Drain LP assets by trading against a pool whose risk parameters were calibrated for a known, trusted set of swappers.
- Circumvent any off-chain compliance or KYC gating the allowlist was meant to enforce.

This is a direct loss-of-LP-principal impact, matching the "allowlist bypass" and "wrong-actor binding" impact classes in scope.

---

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call it. The bypass requires only that the pool admin has allowlisted the router — a natural and expected action for any pool that wants to support the standard periphery flow. No privileged access, no malicious setup, and no non-standard tokens are required.

---

### Recommendation

The extension must check the **original user**, not the intermediary. Two complementary fixes:

1. **Pool-side**: Pass the original initiator through a separate field (e.g., a dedicated `initiator` argument in the extension interface) rather than reusing `msg.sender` of `pool.swap`.

2. **Extension-side (short-term)**: `SwapAllowlistExtension.beforeSwap` should require that `sender` is not a registered factory pool/router, or the pool admin documentation must explicitly warn that allowlisting the router opens the pool to all users.

The cleanest fix is for the pool to forward the true originator (the address that initiated the outermost transaction) rather than its immediate caller, analogous to how `DepositAllowlistExtension` correctly gates on `owner` (the position beneficiary) rather than `sender` (the payer).

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — pool's msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true → passes
8. Bob's swap executes in the restricted pool.
```

Alice's allowlist entry is never consulted. Bob, and every other unpermissioned address, can trade freely by routing through the public router. [3](#0-2) [1](#0-0) [6](#0-5)

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
