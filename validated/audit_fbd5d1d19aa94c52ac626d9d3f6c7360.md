Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract address. If the pool admin allowlists the router — the only way to enable router-mediated swaps for any permitted user — every unpermissioned address can bypass the per-user allowlist by calling any of the router's entry points, exposing the curated pool's LP assets to arbitrary traders.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so the `sender` argument delivered to every extension is the direct caller of `pool.swap()`. [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:**

The value is passed verbatim into `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` and dispatched to every configured extension. [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`:**

`msg.sender` here is the pool (the extension's caller), and `sender` is whoever called `pool.swap()`. The check is `allowedSwapper[pool][sender]`. [3](#0-2) 

**Step 4 — Router calls `pool.swap()` directly, substituting itself as `msg.sender`:**

In `exactInputSingle`, the router stores the originating user only in transient callback context (for payment), but calls `pool.swap()` directly. Inside the pool, `msg.sender` is the router, so `sender` delivered to the extension is the router address, not the originating user. [4](#0-3) 

The same substitution occurs in `exactInput` (all hops): [5](#0-4) 

And in `exactOutputSingle` and `_exactOutputIterateCallback` (all recursive hops): [6](#0-5) 

**Root cause:** The router never encodes the originating user into `extensionData`, and `SwapAllowlistExtension` never decodes it. The only identity the extension sees is the router's address. Once `allowedSwapper[pool][router] = true`, the gate passes for every caller regardless of their individual allowlist status. The check `allowedSwapper[pool][charlie]` is never evaluated.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd addresses, protocol-owned accounts, or whitelisted market makers is fully bypassed by any address routing through `MetricOmmSimpleRouter`. LP principal is exposed to arbitrary swappers, defeating the entire purpose of the allowlist. Because the pool's oracle-anchored pricing is designed for a trusted counterparty set, opening it to arbitrary traders causes adverse selection losses for LPs — a direct loss of LP-owned assets above Sherlock contest thresholds. This matches the "Admin-boundary break bypassed by an unprivileged path" and "direct loss of LP assets" allowed impact categories.

## Likelihood Explanation

The precondition is that the pool admin has added the router to `allowedSwapper[pool][router]`. This is the natural and expected operational step: without it, even individually allowlisted users cannot swap through the supported periphery path. Any production pool intending to allow router-mediated swaps for its permitted users will have taken this step. The attacker requires no special privilege — a single call to `exactInputSingle` with any pool and any `recipient` suffices. The bypass is repeatable on every such pool.

## Recommendation

The extension must resolve the originating user, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Encode the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; `SwapAllowlistExtension.beforeSwap` decodes and checks it when `sender` is a known router. This requires a coordinated convention between the router and the extension.

2. **Maintain a trusted-router registry in the extension**: The extension checks `sender` directly when it is not a known router; when it is a known router, it requires the real user to be attested in `extensionData`.

The simplest safe interim fix is to document that router-mediated swaps are incompatible with per-user `SwapAllowlistExtension` enforcement, remove the router from per-pool allowlists, and require allowlisted users to call `pool.swap()` directly until the extension is updated.

## Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // alice is permitted
3. Pool admin: setAllowedToSwap(pool, router, true)  // router added to enable periphery swaps for alice

Attack
──────
4. charlie (not in allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <curated pool>,
           recipient: charlie,
           ...
       })

5. Router calls pool.swap(charlie, ...) — msg.sender inside pool = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true  ✓  (step 3)

8. Swap executes. charlie receives output tokens.
   allowedSwapper[pool][charlie] is never evaluated.
```

Foundry test plan: deploy pool + extension, configure allowlist as above, call `exactInputSingle` from an address not in the allowlist, assert the swap succeeds and the non-allowlisted address receives output tokens.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
