### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. The pool passes `msg.sender` of the `swap()` call as `sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any user who calls the router on a pool where the router address is allowlisted bypasses the curated access control entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // pool's msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  // checks router, not user
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly: [1](#0-0) 

The pool's `swap` function passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that same `sender` value to the extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [4](#0-3) 

**The impossible choice imposed on pool admins:**

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. But `allowedSwapper[pool][router] = true` grants every user on earth the ability to swap through the router, defeating the allowlist entirely. Conversely, if the admin does not allowlist the router, individually allowlisted users cannot use the router at all — they must call the pool directly. There is no configuration that simultaneously allows specific users to use the router while blocking others.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner explicitly passed by the caller), not the `sender` (the immediate caller): [5](#0-4) 

---

### Impact Explanation

A curated pool that relies on `SwapAllowlistExtension` to restrict trading to approved counterparties (e.g., institutional, KYC'd, or protocol-internal users) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The unauthorized swapper can drain LP-owned token reserves at oracle-quoted prices, causing direct loss of LP principal. The allowlist — the only on-chain mechanism preventing this — provides no protection once the router is allowlisted.

---

### Likelihood Explanation

The router is the primary user-facing swap entry point documented and promoted by the protocol. Any pool admin who wants their allowlisted users to have a normal UX (deadline, slippage, multi-hop) will allowlist the router. The moment they do, the allowlist is void. The attacker needs no special privilege, no flash loan, and no oracle manipulation — a single call to `exactInputSingle` suffices.

---

### Recommendation

Pass the **originating user** through the call chain rather than the immediate caller. Two concrete options:

1. **Router forwards the real sender**: Add a `sender` field to the swap parameters that the router populates with `msg.sender` and the pool passes to extensions instead of its own `msg.sender`.
2. **Extension reads from transient storage**: The router writes the real user into a transient slot before calling the pool; the extension reads it. This mirrors the pattern already used for the callback payer in `MetricOmmSwapRouterBase`.

Either way, `SwapAllowlistExtension.beforeSwap` must compare against the end user's address, not the address of whatever contract called `pool.swap`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists router so users can swap
  allowedSwapper[pool][alice]  = true   // alice is individually approved
  allowedSwapper[pool][bob]    = false  // bob is NOT approved

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → pool.swap(msg.sender = router)
    → _beforeSwap(sender = router)
    → SwapAllowlistExtension.beforeSwap(sender = router)
    → allowedSwapper[pool][router] == true  → passes
    → bob's swap executes, draining LP reserves

Result:
  bob, a disallowed swapper, successfully trades on a curated pool.
  The allowlist provided zero protection.
```

### Citations

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
