### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user's address, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards that address as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user the ability to bypass the per-user restriction.

---

### Finding Description

**Hook binding — wrong actor identity forwarded to the allowlist guard.**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim as the first argument of the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so the extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`. The actual end-user's address is never seen by the guard.

A pool admin who wants to support router-mediated swaps must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that user is individually permitted. The per-user allowlist is silently voided for all router paths.

The same structural problem exists for multi-hop `exactInput` (intermediate hops use `address(this)` as payer, but the pool still receives `msg.sender = router` as the swap initiator) and `exactOutput` / `exactOutputSingle`. [5](#0-4) 

---

### Impact Explanation

**Medium.** A curated pool whose admin has configured `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional market makers) loses that protection entirely for any user who routes through the public router. Unauthorized users can execute swaps against the pool's liquidity, causing adverse selection and direct loss of LP value. The allowlist — the sole on-chain enforcement mechanism for the curation policy — is rendered ineffective on the router path.

---

### Likelihood Explanation

**High.** The router is the standard, documented periphery entry point. Pool admins who deploy a curated pool and want to support normal UX will naturally allowlist the router. The bypass requires no special privilege, no flash loan, and no unusual token behavior — any address can call `exactInputSingle` on the router.

---

### Recommendation

The extension must receive the **original end-user's address**, not the intermediary's address. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should store the original `msg.sender` in transient storage (it already does this for the payer in `_setNextCallbackContext`) and expose it via a callback or pass it as part of `extensionData` so the extension can recover it.

2. **Extension-side:** `SwapAllowlistExtension` should read the true initiator from a trusted source (e.g., a router-signed field in `extensionData`, or a transient-storage slot written by the router before calling the pool) rather than trusting the raw `sender` argument, which is the direct caller of `pool.swap` and not necessarily the economic actor.

Until fixed, pool admins should be warned that allowlisting the router address in `SwapAllowlistExtension` effectively opens the pool to all users.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for allowlisted users.
3. Pool admin does NOT call setAllowedToSwap(pool, userB, true).
   — userB is explicitly not permitted.

Attack
──────
4. userB calls MetricOmmSimpleRouter.exactInputSingle({
       pool:      <curated pool>,
       recipient: userB,
       ...
   });

5. Router calls pool.swap(recipient=userB, ...) with msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension evaluates:
       allowedSwapper[pool][router]  →  true   ✓
   The check passes.

8. userB's swap executes against the curated pool's liquidity.
   The per-user allowlist was never consulted.
```

The corrupted value is `sender = router` where the guard intended `sender = userB`. This is the direct analog of the ERC-7683 bug: the wrong address (router instead of actual user) is used in the field that determines which identity the guard evaluates, silently voiding the protection for all router-mediated flows.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
