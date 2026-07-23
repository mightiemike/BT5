Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` the extension sees — is the router contract, not the end user. If the pool admin allowlists the router so that legitimate users can reach the pool through it, every non-allowlisted user can also bypass the gate by routing through the same public contract, rendering the allowlist completely ineffective for router-mediated swaps.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`:**

In `MetricOmmPool.swap`, the `sender` argument forwarded to the extension hook is `msg.sender` — whoever called `pool.swap()`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`:**

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. The check is therefore on the immediate caller of the pool, not the end user: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees:** [4](#0-3) 

**Step 5 — The real end-user is stored only in transient storage for callback payment, never forwarded to the pool as the swap initiator:** [5](#0-4) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`. [6](#0-5) 

**Root cause:** The allowlist check operates on one identity (the router address), while the economic action (the swap) is performed on behalf of a different identity (the end user). The discrepancy is structural: the pool has no mechanism to receive or verify the real initiator behind the router.

**Existing guards are insufficient:** The `allowAllSwappers` flag is a separate bypass path and does not help. The `allowedSwapper` mapping is keyed by `[pool][sender]` where `sender` is always the router for router-mediated swaps. There is no secondary check on `recipient` or any other end-user identity.

## Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swapping to a curated set of addresses. To allow those users to reach the pool through the public `MetricOmmSimpleRouter`, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **every address on the network** can bypass the gate by calling any of the router's entry points. The allowlist is rendered completely ineffective for router-mediated swaps, which is the primary user-facing path. This constitutes broken core pool functionality — the access control extension provides no protection whatsoever once the router is allowlisted, directly impacting pools relying on the allowlist for regulatory compliance or LP-protection.

## Likelihood Explanation

The router is a public, permissionless contract. Any user who discovers that the router is allowlisted on a restricted pool can immediately exploit the bypass with no special privileges, no flash loan, and no complex setup. The pool admin has no way to selectively allow legitimate users to use the router without simultaneously opening the gate to all users, so the bypass is reachable whenever the pool is intended to be usable through the router at all.

## Recommendation

The extension must gate on the **end user**, not the immediate caller of `pool.swap()`. Two complementary approaches:

1. **Pass the real initiator through the pool.** Add an `initiator` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension then checks `initiator` instead of `sender`. This requires a pool-level or router-level convention.

2. **Check `sender` and `recipient` together.** For router-mediated swaps the recipient is often the end user. The extension could require that either `sender` or `recipient` is allowlisted, closing the gap for the common case where the user is also the recipient.

3. **Allowlist the router with a separate per-user check inside the router.** The router could enforce its own allowlist before calling the pool, and the extension could trust the router as a gating intermediary. This requires the router to be a trusted, non-upgradeable contract and the extension to be aware of it.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution path:
    router.exactInputSingle()
      → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, bob, tokenIn)  // bob stored in transient storage only
      → pool.swap(recipient=bob, ...)         // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result:
  bob swaps successfully despite never being allowlisted.
  The allowlist invariant is broken for all router-mediated swaps.

Foundry test plan:
  1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  2. Set allowedSwapper[pool][alice] = true and allowedSwapper[pool][router] = true.
  3. Call router.exactInputSingle from bob (not allowlisted).
  4. Assert swap succeeds and bob receives output tokens.
  5. Confirm bob is not in allowedSwapper[pool] mapping.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
