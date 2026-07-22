### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to support standard UX inadvertently opens the gate for every user on-chain, completely defeating the allowlist.

---

### Finding Description

**Root cause — wrong actor bound in `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool): [3](#0-2) 

**Router path — user identity is lost:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The router is `msg.sender` to the pool; the actual user's address is stored only in transient storage for the payment callback and is never forwarded to the pool as `sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

**Result — impossible choice for the pool admin:**

| Admin action | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; broken UX |
| **Allowlist the router** | Every user on-chain can call `router.exactInputSingle` and bypass the allowlist |

There is no configuration that simultaneously supports router-mediated swaps and enforces per-user restrictions.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a curated, permissioned venue — e.g., restricted to KYC'd counterparties, specific protocols, or institutional traders. Once the router is allowlisted (the only way to support standard UX), any unprivileged address can trade on the pool by routing through `MetricOmmSimpleRouter`. This:

- Exposes LP funds to toxic flow the allowlist was designed to block.
- Breaks the core pool invariant that only approved actors may swap.
- Constitutes a direct, fund-impacting bypass of a configured protection hook.

Severity: **High** — broken core pool functionality / allowlist guard fails open for all router-mediated swaps.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entry point.
- Any user who discovers the pool is allowlist-gated can trivially route through the router instead of calling the pool directly.
- No privileged access, no special tokens, no admin cooperation required beyond the pool admin having allowlisted the router for legitimate users.

---

### Recommendation

1. **Pass the originating user through the router.** Add a `swapper` field to each router swap call and forward it to the pool via `extensionData`. The `SwapAllowlistExtension` should decode and check that field instead of (or in addition to) `sender`.

2. **Alternatively, check `sender` against the router and then require a user-level proof in `extensionData`** (e.g., a signed permit or an on-chain registry lookup keyed by the actual EOA).

3. **Document the limitation clearly** in `SwapAllowlistExtension` NatSpec: the current `sender` check is only meaningful for direct pool calls; router-mediated swaps present the router address as `sender`.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)            // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → check PASSES
    → bob's swap executes on the allowlisted pool

Verification:
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender=bob, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][bob] == false
    → reverts NotAllowedToSwap                         // direct call correctly blocked

  Conclusion: router path bypasses the per-user allowlist entirely.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
