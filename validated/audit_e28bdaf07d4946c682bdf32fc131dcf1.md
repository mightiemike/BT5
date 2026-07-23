### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any unprivileged user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the end user. A pool admin who allowlists the router to enable standard periphery usage inadvertently opens the allowlist to every user on the network.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call payload: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension sees `sender = router_address`, not the end user. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: allowlisted users cannot use the standard periphery at all (broken UX, their router calls revert with `NotAllowedToSwap`).
- **If the router IS allowlisted** (to restore periphery access for legitimate users): every address on the network can call `router.exactInputSingle(pool, ...)` and the extension sees `sender = router`, which is allowlisted — the per-user gate is completely bypassed.

There is no way to simultaneously allow legitimate users to use the router and block non-allowlisted users, because the extension has no visibility into the actual end user's address.

### Impact Explanation

Any user can trade on a pool that was configured to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted institutions). The attacker simply routes through `MetricOmmSimpleRouter` instead of calling `pool.swap()` directly. This is a direct policy bypass with fund-impacting consequences: the pool's liquidity is exposed to actors the pool admin explicitly intended to exclude, and the pool admin's curation invariant is silently broken.

### Likelihood Explanation

The router is the canonical, documented periphery entry point. Any user who discovers the allowlist blocks their direct call will naturally try the router. No special privileges, no malicious setup, and no non-standard tokens are required — only a standard `exactInputSingle` call through the deployed `MetricOmmSimpleRouter`.

### Recommendation

The extension must gate on the actual end user, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` against the router and then verify the user from transient storage**: The router stores the original payer in transient storage (already done for callback settlement); the extension could read it via a trusted router interface.

3. **Gate on `sender` but treat the router as a transparent forwarder**: Require the router to pass the original user as `recipient` and have the extension check `recipient` instead of `sender` when `sender` is a known router. This is fragile but simpler.

The cleanest fix is option 1: the router always appends the original `msg.sender` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a recognized router.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary so that allowlisted users can use the standard periphery)
  - Pool admin calls setAllowedToSwap(pool, alice, true)
    (alice is the intended curated user)
  - Bob is NOT allowlisted

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender=router
  5. Checks: allowedSwapper[pool][router] == true → PASSES
  6. Bob's swap executes on the curated pool despite not being allowlisted

Result: Bob trades on a pool that was intended to be restricted to alice only.
        The pool admin's curation invariant is broken with zero privileges required.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
