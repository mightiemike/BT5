### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the router's allowlist entry rather than the actual user's. A pool admin who allowlists the router (the natural step to enable router-mediated swaps for permitted users) inadvertently opens the pool to every user on the router, completely defeating the per-user allowlist.

---

### Finding Description

**Call chain for a direct swap:**
```
User → pool.swap(sender=User) → _beforeSwap(sender=User) → SwapAllowlistExtension.beforeSwap(sender=User)
```
Extension checks: `allowedSwapper[pool][User]` ✓ correct.

**Call chain through the router:**
```
User → MetricOmmSimpleRouter.exactInputSingle → pool.swap(msg.sender=Router) → _beforeSwap(sender=Router) → SwapAllowlistExtension.beforeSwap(sender=Router)
```
Extension checks: `allowedSwapper[pool][Router]` — the user's identity is lost.

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same applies to every hop in `exactInput` (the router is always the direct caller of each pool): [5](#0-4) 

And in the recursive `exactOutput` callback path, the router again calls each pool directly: [6](#0-5) 

The result is a two-sided trap:

| Admin action | Effect |
|---|---|
| Allowlist only specific users (not the router) | Allowlisted users cannot use the router at all — broken UX |
| Allowlist the router (to enable router swaps) | Every user on the router bypasses the per-user gate |

There is no configuration that simultaneously enforces per-user allowlisting **and** supports the router.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, whitelisted arbitrageurs, or KYC'd addresses) loses its access control entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP reserves. LP funds are directly at risk from adverse selection, MEV, or trades the pool was specifically designed to exclude. This is a direct loss of LP principal above Sherlock thresholds.

---

### Likelihood Explanation

The router is the canonical user-facing entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps for its permitted users must allowlist the router — the exact configuration that triggers the bypass. The trigger requires no privileged access beyond the pool admin's own intended setup, and no non-standard tokens or malicious contracts.

---

### Recommendation

The extension must recover the original user identity rather than relying on the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct value.

2. **Check `sender` against a router registry and then verify the payer stored in the router's transient context**: The extension calls back into the router to retrieve the actual payer stored in `T_SLOT_PAY_PAYER` (set by `_setNextCallbackContext`), and gates on that address instead of `sender`.

3. **Require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with the router and enforce this at the extension level by reverting when `sender` is a known router address.

The cleanest long-term fix is option (1) with the router signing or encoding the original `msg.sender` into `extensionData`, and the extension verifying the router's identity via `msg.sender` (the pool) cross-referencing a trusted router registry on the factory.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists Alice: setAllowedToSwap(pool, Alice, true)
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
    (necessary so Alice can use the router)

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool=pool, recipient=Bob, ...
    )
  - Router calls pool.swap(recipient=Bob, ...) — msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Bob's swap executes against LP reserves

Result:
  Bob, who is not on the allowlist, successfully swaps against the curated pool.
  The per-user allowlist is completely bypassed.
  LP funds are exposed to any user who routes through MetricOmmSimpleRouter.
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
