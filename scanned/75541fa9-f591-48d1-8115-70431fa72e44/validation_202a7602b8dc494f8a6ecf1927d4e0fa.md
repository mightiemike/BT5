### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards that address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual end-user. If the router is allowlisted (the only way to let any user use the router on a curated pool), every unprivileged address can bypass the swap allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the namespace key) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The actual end-user is stored only in the transient callback context (as the payer), never surfaced to the extension: [5](#0-4) 

**Result**: the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the check passes for every caller regardless of their individual allowlist status, because the extension cannot distinguish between different end-users behind the same router address.

The same structural flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with `msg.sender = router`. [6](#0-5) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted counterparties loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP funds, exposing LPs to counterparties the pool admin explicitly intended to exclude. This is a direct admin-boundary break with fund-impacting consequences: LP principal is at risk from trades by actors the allowlist was designed to block.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint documented in the periphery layer. Any pool admin who wants allowlisted users to use the router must allowlist the router, which simultaneously opens the pool to all users. The trigger requires no special privileges, no malicious setup, and no non-standard tokens — any public caller can reach it in a single transaction.

---

### Recommendation

The extension must recover the original transaction initiator rather than the immediate pool caller. Two sound approaches:

1. **Pass the original sender through the router**: have `MetricOmmSimpleRouter` include the real `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it (with a signature or trusted-forwarder pattern).
2. **Check `tx.origin` as a fallback** (acceptable only if the pool is not intended to be called from other contracts): replace the `sender` check with `tx.origin` inside the extension when `sender` is a known router.
3. **Preferred — pool-level sender override**: add a `realSender` field to the swap call that the router populates with `msg.sender`, and have the pool forward it to extensions as a distinct argument from the immediate caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    → msg.sender of pool.swap() = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes for bob despite bob not being on the allowlist.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; bob trades against LP funds on a curated pool.
``` [7](#0-6) [8](#0-7) [4](#0-3)

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
