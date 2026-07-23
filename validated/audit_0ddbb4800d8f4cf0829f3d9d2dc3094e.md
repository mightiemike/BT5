### Title
`SwapAllowlistExtension` Swap Guard Checks Router Identity Instead of Actual Swapper, Allowing Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the address that called `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every unprivileged address can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the immediate pool caller) is allowlisted: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` to the pool: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Two broken outcomes follow:

1. **Bypass**: If the admin adds the router to the allowlist (to permit router-mediated swaps for legitimate users), every address — including those explicitly excluded — can swap by calling the router.
2. **Lock-out**: If the admin does not add the router, individually allowlisted users are silently blocked from using the router even though they are permitted.

Contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` (the economically relevant actor for deposits), not `sender`: [5](#0-4) 

The swap extension applies the same pattern but to the wrong identity.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any address can call `router.exactInputSingle` / `exactInput` / `exactOutput` and the guard passes because it sees the router, not the caller. Unauthorized swappers can drain LP-owned token balances at oracle-quoted prices, directly reducing LP principal.

---

### Likelihood Explanation

The router is the standard user-facing entry point. A pool admin who wants to allow normal users to swap through the router must allowlist it. The admin has no on-chain signal that doing so opens the allowlist to everyone; the extension's name and NatSpec ("Gates `swap` by swapper address") imply per-user control. The misconfiguration is therefore a predictable operational mistake, not a contrived edge case.

---

### Recommendation

Inside `beforeSwap`, the extension must recover the original end-user rather than trusting `sender`. Two options:

1. **Pass the real user via `extensionData`**: require callers (router, direct) to encode the actual swapper in `extensionData` and verify it against a signature or trusted forwarder registry.
2. **Mirror `DepositAllowlistExtension`**: define a separate `originalSender` field that the router populates in `extensionData`, and gate on that field instead of `sender`.

The simplest safe default is to treat `sender == router` as a blocked identity unless the admin explicitly opts into router-open mode via `setAllowAllSwappers`, making the trade-off explicit.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1).
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only permitted swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // admin enables router-mediated swaps for alice

Attack
──────
4. bob (not allowlisted) calls router.exactInputSingle({pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...)
   → pool calls _beforeSwap(msg.sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓ passes
   → bob's swap executes; LP funds transferred to bob

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; bob receives pool tokens
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
