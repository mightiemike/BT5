### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If a pool admin allowlists the router (the natural configuration to support router-mediated swaps for their curated users), every unprivileged user can bypass the per-user allowlist by calling through the router.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks `sender` (the router), not the original user.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the immediate caller of the pool) as the identity being gated: [3](#0-2) 

**Step 3 — The router calls the pool directly, substituting itself as `sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with the router as `msg.sender`. The original user's address is never forwarded to the pool or the extension: [4](#0-3) 

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.**

When a pool admin wants their allowlisted users to be able to use the router (the natural UX expectation), they must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check at line 37 of `SwapAllowlistExtension` passes for every caller of the router, regardless of whether that caller is individually allowlisted. [5](#0-4) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) targeting the restricted pool and execute swaps at oracle prices. This constitutes a broken core pool functionality: the allowlist guard that the pool admin configured to protect LP funds and control who trades against the pool is silently inoperative for all router-mediated paths. LP assets are exposed to unrestricted trading, which is a direct loss of the protection the pool was designed to enforce.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any pool admin who wants their allowlisted users to have a normal UX (i.e., not require direct pool calls) will add the router to the allowlist. This is the expected operational configuration. Once done, the bypass is trivially reachable by any address with no special privileges, no malicious setup, and no non-standard tokens.

### Recommendation

The router must forward the original `msg.sender` to the pool so the extension can gate the economically relevant actor. One approach: add an explicit `payer`/`originator` field to the swap call that the pool passes to extensions as `sender` instead of `msg.sender`. Alternatively, the `SwapAllowlistExtension` should check the `sender` argument only when it differs from a known router, or the pool should use a transient-storage originator pattern (analogous to how the liquidity adder stores `payer` in transient storage) so the extension always sees the end user.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  allowedSwapper[pool][alice] = true          // alice is the only allowed swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)         // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes at oracle price
        → router.metricOmmSwapCallback() pulls tokens from bob

Result:
  bob successfully swaps on a pool he is not allowlisted for.
  The allowlist guard is completely bypassed.
  alice's LP position is exposed to unrestricted trading.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-42)
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
