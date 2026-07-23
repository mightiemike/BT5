### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged swapper to bypass a curated pool's allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted, not the actual user. If the pool admin allowlists the router (the only way to let users use the standard interface), every user — including explicitly disallowed ones — can bypass the per-user allowlist by routing through it.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

The result is a forced dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Allowlist the router address | Every user — including explicitly blocked ones — can swap by routing through the router |
| Do not allowlist the router | Individually allowlisted users cannot use the standard router interface at all |

Neither option preserves the intended per-user curation policy.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol partners). A disallowed user bypasses the guard entirely by calling `MetricOmmSimpleRouter.exactInputSingle`. The extension sees `sender = router`, which is allowlisted, and permits the swap. The disallowed user trades against the pool's LP assets at oracle-derived prices, causing direct loss of LP principal or fee revenue that the curation policy was designed to prevent. This matches the "High direct loss or curation failure if disallowed users can still trade" impact gate.

---

### Likelihood Explanation

The bypass requires no special privilege, no malicious setup, and no non-standard token. Any user who knows the pool uses `SwapAllowlistExtension` can route through the public `MetricOmmSimpleRouter` in a single transaction. The router is the standard, documented entrypoint for swaps, so the bypass path is the default user flow.

---

### Recommendation

The extension must gate the **ultimate economic actor**, not the direct pool caller. Two sound approaches:

1. **Pass the original user through the router.** The router already stores the original `msg.sender` in transient storage as the payer. The pool could expose a `swapInitiator()` view (reading transient state) and the extension could call it, or the router could pass the real user as `recipient`-equivalent data in `extensionData`.

2. **Check `sender` only when the caller is not a known router.** The extension can maintain a registry of trusted routers; when `sender` is a trusted router, it reads the real user from `extensionData` (which the router must populate). This is the pattern used by Uniswap v4 hooks.

The simplest safe fix: require that `pool.swap()` callers who are routers inject the real user address into `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a registered router.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can use the standard interface.
3. Admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
   // bob is NOT added to the allowlist
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=bob, ...)  with msg.sender = router
6. Pool calls extension.beforeSwap(sender=router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes against LP assets despite being explicitly excluded.
```

Bob never interacts with the pool directly; the standard router is sufficient to bypass the allowlist.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
