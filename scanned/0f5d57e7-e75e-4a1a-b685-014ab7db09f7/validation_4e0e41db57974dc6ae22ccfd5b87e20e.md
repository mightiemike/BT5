### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any user to bypass the swap allowlist or rendering the router unusable for allowlisted users — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two mutually exclusive broken states: if the router is allowlisted, every user on the public internet bypasses the guard; if the router is not allowlisted, every allowlisted user is blocked from using the standard periphery.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← WRONG identity checked
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the user goes through the router, the router calls `pool.swap()` directly with no forwarding of the original user's address: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is that the extension's allowlist lookup is keyed on the **router contract address**, not the **end-user address**. The pool admin has no way to configure the extension to distinguish between individual users who arrive via the router.

---

### Impact Explanation

Two fund-impacting outcomes, both reachable by any unprivileged user:

**Outcome A — Full allowlist bypass (router is allowlisted):**
A pool admin who wants to restrict swaps to KYC'd or otherwise vetted addresses naturally allowlists the router so that vetted users can use the standard interface. Once the router is allowlisted, `allowedSwapper[pool][router] = true`, and the `beforeSwap` guard passes for every caller regardless of their identity. Any address on the public internet can swap on the restricted pool, defeating the entire access-control invariant. This is an admin-boundary break: the pool admin's configured guard is bypassed by an unprivileged path (the public router).

**Outcome B — Core swap functionality broken (router is not allowlisted):**
If the admin does not allowlist the router, vetted users who call `exactInputSingle` or any multi-hop route receive `NotAllowedToSwap` even though their address is individually allowlisted. The only usable path is a direct `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` — not a realistic option for ordinary EOAs. This breaks the core swap flow for the pool's intended user base.

Both outcomes are reachable without any privileged action by the attacker.

---

### Likelihood Explanation

**Medium.** The `SwapAllowlistExtension` is a production periphery contract explicitly listed as a reference implementation for access-controlled pools. Any pool that deploys it and expects users to interact through `MetricOmmSimpleRouter` (the standard entry point) will encounter one of the two broken states. No special timing, mempool monitoring, or front-running is required — the bypass is structural and reproducible on every swap through the router.

---

### Recommendation

The extension must check the **original end-user identity**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a coordinated change to the router and the extension.

2. **Check `recipient` instead of `sender` (partial fix):** For single-hop swaps where the user is also the recipient, checking `recipient` would gate the correct address. This breaks for multi-hop routes where intermediate recipients are the router itself.

The cleanest fix is approach 1, with the router always prepending the original caller's address to `extensionData` so that any guard extension can recover the true swapper identity.

---

### Proof of Concept

**Bypass scenario (Outcome A):**

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so vetted users can use it
ext.setAllowedToSwap(address(pool), address(router), true);
// Alice (vetted) and Bob (not vetted) are NOT individually listed

// Bob (unauthorized) calls the router
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    ...
}));
// pool.swap() is called with msg.sender = router
// _beforeSwap(sender=router, ...) → allowedSwapper[pool][router] = true → PASSES
// Bob's swap executes on the restricted pool — allowlist bypassed
```

**Broken functionality scenario (Outcome B):**

```solidity
// Setup
ext.setAllowedToSwap(address(pool), alice, true); // Alice is vetted
// Router is NOT allowlisted

// Alice tries to use the standard interface
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: alice,
    ...
}));
// pool.swap() called with msg.sender = router
// _beforeSwap(sender=router, ...) → allowedSwapper[pool][router] = false → REVERTS NotAllowedToSwap
// Alice cannot use the router despite being individually allowlisted
```

The root cause is the identity mismatch at: [6](#0-5) 

combined with the router never forwarding the original caller's address: [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
