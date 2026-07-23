### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router When Router Address Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` at the pool is the **router**, not the end user. If the pool admin allowlists the router address (a necessary step to allow any router-mediated swap), every user — including those not individually allowlisted — can bypass the gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` at the pool: [4](#0-3) 

Consequence: the identity the extension actually checks is the **router address**, not the end user. A pool admin who wants to allow router-mediated swaps for their approved users must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for **every** caller of the router, including users who are not individually approved. The allowlist is silently reduced to a binary "router allowed / router blocked" switch with no per-user granularity.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also originate from the router: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties (e.g., institutional LPs controlling who trades against their liquidity). Once the router is allowlisted, any address can call `exactInputSingle` or `exactInput` and execute swaps at oracle-derived bid/ask prices. Unauthorized swappers can extract value from LP bins at the oracle mid, causing direct loss of LP principal. The pool's core access-control invariant — that only approved addresses may trade — is broken for all router-mediated paths.

---

### Likelihood Explanation

The trigger is a routine, non-malicious admin action: adding the router to the allowlist so that approved users can interact via the standard periphery. No privileged escalation, oracle manipulation, or non-standard token is required. Any user who discovers the router is allowlisted can immediately exploit it. The `MetricOmmSimpleRouter` is a public, permissionless contract, so the bypass is trivially reachable.

---

### Recommendation

The extension must verify the **end user**, not the immediate caller. Two sound approaches:

1. **Pass the original user through the router.** Add a `swapper` field to the extension data that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field instead of (or in addition to) `sender`.

2. **Check `sender` against the allowlist; if `sender` is the router, require the router to attest the real user via signed extension data or a dedicated router-aware allowlist entry.** This keeps the extension stateless while preserving per-user granularity.

Either way, the extension must not treat the router address as a proxy for the end user's identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is approved)
  allowedSwapper[pool][router] = true   (admin adds router so alice can use it)
  allowedSwapper[pool][bob]    = false  (bob is NOT approved)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap receives sender=router
    → checks allowedSwapper[pool][router] == true  ✓
    → swap proceeds; bob extracts tokens from LP bins at oracle price

Result:
  bob, who is explicitly not allowlisted, completes a swap in a restricted pool.
  LP funds are transferred to bob at oracle-derived prices.
  alice's individual allowlist entry is irrelevant; the router entry grants access to all.
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
