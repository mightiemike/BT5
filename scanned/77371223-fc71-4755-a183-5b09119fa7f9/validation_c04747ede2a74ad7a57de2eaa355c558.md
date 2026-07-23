### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps on a curated pool), every user — including non-allowlisted ones — can bypass the guard by calling through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly — it does not forward the original `msg.sender` (the end user) to the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Bypass path:** A pool admin who wants to allow router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router address is allowlisted, the extension's `beforeSwap` check passes for every caller — allowlisted or not — because the extension only sees `sender = router`. Any unprivileged user can then call `router.exactInputSingle()` and trade on a pool that was supposed to be restricted.

**Broken-usability path (secondary):** If the admin allowlists individual user EOAs but not the router, those users cannot use the router at all — the extension sees `sender = router` (not allowlisted) and reverts. This forces all curated-pool users to call the pool directly, breaking the standard periphery flow.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled bots) loses that restriction entirely once the router is allowlisted. Any user can execute swaps at oracle-derived prices against LP positions, causing direct loss of LP principal through unauthorized trades the LPs never consented to allow. This matches the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds" impact categories.

---

### Likelihood Explanation

Likelihood is high. The `MetricOmmSimpleRouter` is the canonical user-facing swap interface. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address — there is no other mechanism. The moment they do, the guard is fully open to all users. The attacker needs no special privileges, no malicious setup, and no non-standard tokens; a single call to `exactInputSingle` suffices.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must check the economically relevant actor — the end user — not the intermediary contract. Two complementary fixes:

1. **Pass the original initiator through the pool.** The pool could forward an additional `initiator` field (the original `tx.origin` or a router-supplied caller) alongside `sender`. The extension would then check `initiator` instead of `sender`.

2. **Router-level identity forwarding.** The router could encode the original `msg.sender` in `extensionData` and the extension could decode and verify it (with the pool verifying the router's identity before trusting the encoded value).

The simplest safe fix that requires no core changes: the extension should revert when `sender` is a known router/intermediary unless the pool has explicitly set `allowAllSwappers = true`, and the admin documentation must warn that allowlisting the router address opens the gate to all users.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension configured.
// Admin allowlists the router so that allowed users can swap via the router.
// Result: any user (attacker) can also swap via the router.

function test_swapAllowlistBypassViaRouter() public {
    // Admin allowlists the router address (required for router-mediated swaps)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Also allowlist a legitimate user for deposit
    depositExtension.setAllowedToDeposit(address(pool), address(legitimateDepositor), true);

    // Legitimate depositor adds liquidity directly
    vm.prank(legitimateDepositor);
    pool.addLiquidity(legitimateDepositor, 0, deltas, "", "");

    // Attacker is NOT in the allowlist
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker bypasses the allowlist by routing through the router
    // The extension sees sender=router (allowlisted), not attacker (not allowlisted)
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: attacker,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Attacker successfully swapped despite not being allowlisted
    assertGt(amountOut, 0, "attacker bypassed swap allowlist via router");
}
``` [3](#0-2) [6](#0-5) [4](#0-3)

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
