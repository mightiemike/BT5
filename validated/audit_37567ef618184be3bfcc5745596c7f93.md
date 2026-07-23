### Title
`SwapAllowlistExtension` Bypass via Router Intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every user — including explicitly disallowed ones — can bypass the per-pool swap allowlist by calling any router entry point.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router contract address**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, including addresses that were explicitly denied. The allowlist is fully neutralised for the router path.

The same identity substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops through `_exactOutputIterateCallback`): [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted integrators). Any address not in the allowlist is supposed to be blocked from swapping. Because the router substitutes its own address for the user's address in the `sender` field seen by the extension, a single router allowlist entry opens the gate for every user of the router. Disallowed users can execute swaps, drain liquidity at oracle-derived prices, and extract value from a pool that was explicitly configured to prevent them from doing so. This is a direct loss of the curation guarantee and a breach of the pool's access-control invariant.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for end-user swaps. Pool admins who deploy a `SwapAllowlistExtension` and want their allowlisted users to be able to use the router have no choice but to add the router to the allowlist — there is no mechanism to forward the originating user's identity through the router to the extension. The bypass is therefore reachable on any production pool that (a) uses `SwapAllowlistExtension` and (b) permits router-mediated swaps, which is the expected operational configuration.

---

### Recommendation

The `sender` identity forwarded to `beforeSwap` must reflect the economic actor, not the immediate `msg.sender`. Two complementary fixes:

1. **Router-level**: Have the router encode the originating user's address in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when the immediate `sender` is a known router. This requires a trusted router registry or a signed-user-identity scheme.

2. **Extension-level (simpler)**: Add a `trustedForwarder` mapping to `SwapAllowlistExtension`. When `sender` is a registered forwarder, decode the real user from `extensionData` and check that address instead. Pool admins register the router as a forwarder, and the router always appends `abi.encode(msg.sender)` to `extensionData` before calling the pool.

Either approach must ensure the extension always resolves to the address that controls the economic outcome of the swap, not the intermediate contract that relays the call.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // allow router so users can swap
3. Pool admin calls setAllowedToSwap(pool, alice, false)   // alice is explicitly denied
   (alice is never added; default mapping value is false)

Attack
──────
4. Alice calls MetricOmmSimpleRouter.exactInputSingle({
       pool:        <pool address>,
       recipient:   alice,
       zeroForOne:  true,
       amountIn:    X,
       ...
   });

Execution trace
───────────────
5. Router calls pool.swap(alice, true, X, ..., extensionData)
   → msg.sender to pool = router

6. pool.swap() calls _beforeSwap(msg.sender=router, ...)

7. ExtensionCalling passes sender=router to SwapAllowlistExtension.beforeSwap

8. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← router is allowlisted
   → check passes, no revert

9. Swap executes. Alice receives tokens from a pool she is explicitly denied access to.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [7](#0-6) [8](#0-7) [9](#0-8)

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
