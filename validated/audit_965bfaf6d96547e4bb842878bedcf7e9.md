Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-based access for legitimate users simultaneously opens the allowlist to every user who routes through the router, completely defeating per-user curation.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230, passing its own `msg.sender` as `sender`. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value verbatim into the encoded extension call. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the original `msg.sender` (the user) is stored only in transient callback context for payment purposes and is never forwarded to the pool as the swap initiator. [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Resulting call chain:**
```
User → Router.exactInputSingle(pool, ...)
     → Pool.swap(recipient, ...)   [msg.sender = Router]
     → ExtensionCalling._beforeSwap(sender = Router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = Router)
     → checks allowedSwapper[pool][Router] → true
     → swap executes for any user
```

The extension never observes the originating user address. There are no existing guards that recover the true initiator — the `extensionData` field is passed through but the extension does not decode it, and no on-chain mechanism binds the original caller into the hook arguments.

## Impact Explanation
Any unprivileged user can bypass a curated pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant). Once the router is allowlisted — a necessary operational step for any pool that wants allowlisted users to use the standard router — `allowedSwapper[pool][router] == true` causes the check to pass for every caller regardless of their individual allowlist status. Unauthorized users can execute swaps against a pool intended to be restricted (e.g., KYC-gated), causing direct loss of LP assets and breaking the core curation invariant. This constitutes a direct loss of user principal and LP assets above Sherlock thresholds.

## Likelihood Explanation
The router is the primary user-facing entry point. Any pool admin operating a curated pool who wants allowlisted users to access it via the standard UX must add the router to `allowedSwapper`. This is not a corner case or misconfiguration — it is the only way to enable router-based swaps on an allowlisted pool. Once the router is added (which is expected in normal operation), the bypass is immediately available to all users with zero additional preconditions. The attack is repeatable and requires no special privileges.

## Recommendation
The extension must gate on the economically relevant actor, not the intermediary. The preferred fix is to have the router encode `msg.sender` into `extensionData` before calling the pool, and have the extension decode and verify this address. The pool admin allowlists individual users, not the router. To prevent a malicious router from supplying a forged sender, the extension can verify that `msg.sender` (the pool) is a known factory pool and that the encoded sender is consistent with the pool's recorded callback payer (already stored in transient storage by the router). Alternatively, redesign the allowlist to gate on `recipient` if output-side gating matches the admin's intent, though this is semantically different from sender-side gating.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, allowedUser, true).
3. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so allowedUser can use the router.
4. unauthorizedUser calls:
     router.exactInputSingle({pool: pool, recipient: unauthorizedUser, ...})
5. Router calls pool.swap(unauthorizedUser, ...) with msg.sender = router.
6. Pool calls _beforeSwap(sender = router, ...).
7. Extension checks allowedSwapper[pool][router] → true.
8. Swap executes. unauthorizedUser receives output tokens.
   The per-user allowlist was never consulted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
