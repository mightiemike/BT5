### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (the natural step to let their allowlisted users trade via the standard periphery), every unpermissioned user can bypass the allowlist by calling the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router] → true → passes
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address, not the originating user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`.

**The impossible choice forced on pool admins:**

| Admin action | Result |
|---|---|
| Allowlist the router | Every unpermissioned user can swap by calling the router |
| Do not allowlist the router | Individually allowlisted users cannot use the standard router at all |

Neither option correctly enforces per-user gating through the supported periphery path.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost: they call the public router with the target pool address and receive output tokens from a pool that was designed to exclude them. This constitutes a direct admin-boundary break — an unprivileged path (the public router) bypasses a configured access-control guard — and can result in unauthorized extraction of pool liquidity at oracle-derived prices.

---

### Likelihood Explanation

Likelihood is **High**. The `MetricOmmSimpleRouter` is the standard, documented periphery entry point. Any pool admin who wants their allowlisted users to trade via the router must allowlist the router address, which is the natural and expected configuration. The bypass requires no special knowledge beyond knowing the router address and the pool address, both of which are public.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the originating user through the router.** Add a `swapperOverride` field to `extensionData` that the router populates with `msg.sender` before forwarding. The extension reads and verifies this field only when `sender` is a known trusted router (verified against the factory registry).

2. **Check `tx.origin` as a fallback only when `sender` is a trusted router.** This is simpler but carries the standard `tx.origin` caveats; it is acceptable if the threat model excludes contract-based callers.

3. **Require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap` directly. This is the zero-code fix but breaks UX.

The cleanest production fix is option 1: the router explicitly encodes the originating user in `extensionData`, and the extension verifies the router's identity before trusting that field.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can trade via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker is NOT individually allowlisted.
// Attacker calls the public router — sender seen by the extension is the router, not the attacker.
vm.startPrank(attacker);
token1.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        recipient:       attacker,
        zeroForOne:      false,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
vm.stopPrank();
// Swap succeeds: extension checked allowedSwapper[pool][router] == true,
// never inspecting the attacker's address.
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
