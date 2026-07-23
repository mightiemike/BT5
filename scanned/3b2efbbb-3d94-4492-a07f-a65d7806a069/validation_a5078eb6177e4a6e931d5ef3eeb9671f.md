### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end-user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user in the world can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When the user enters through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

So the extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`. The end-user's address is never consulted.

**The dilemma this creates for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot swap through the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the per-user gate by routing through the router |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers). The admin must allowlist the router to support normal user flows. Once the router is allowlisted, any arbitrary address can call `router.exactInputSingle(...)` and trade against the pool's liquidity, completely defeating the allowlist. LP funds are exposed to unauthorized traders, and any price-impact or fee-extraction that the allowlist was meant to prevent becomes freely executable.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who discovers the allowlist is enforced on direct `pool.swap()` calls can trivially route through the router instead. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must recover the **originating user** rather than the direct pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router as a dedicated field.** The router already stores the payer in transient storage (`_getPayer()`). The pool could expose a `swapWithOriginator(address originator, ...)` entry point, or the extension could read the router's transient payer slot. This requires a coordinated interface change.

2. **Check `sender` against the allowlist only when `sender` is not a known periphery contract; otherwise revert.** This is fragile and not recommended.

3. **Most robust:** redesign `SwapAllowlistExtension` to accept an `originator` field inside `extensionData` that the router populates with `msg.sender` before calling the pool, and verify it with a trusted-router registry. The extension then gates on `originator`, not on `sender`.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists the router so normal users can trade
ext.setAllowedToSwap(pool, address(router), true);
// Admin does NOT allowlist attacker
// ext.setAllowedToSwap(pool, attacker, false);  // default

// Attacker bypasses the allowlist:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds — SwapAllowlistExtension saw sender=router, which is allowlisted.
// Attacker traded against LP funds without being on the allowlist.
```

The `beforeSwap` hook receives `sender = address(router)`, looks up `allowedSwapper[pool][router] == true`, and passes. The attacker's address is never checked. [5](#0-4) [6](#0-5)

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
