### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` — the **direct caller of `pool.swap()`**. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed on an allowlisted pool), every user — including those explicitly excluded from the allowlist — can bypass the per-user gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then enforces the allowlist against that `sender` argument: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly: [4](#0-3) 

At that point the pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. If the pool admin has allowlisted the router address (the only way to permit any router-mediated swap on an allowlisted pool), the check passes for **every caller of the router**, regardless of whether that caller is individually permitted.

The same structural problem applies to multi-hop `exactInput` and `exactOutput` paths, and to `simulateSwapAndRevert`, all of which call `pool.swap` with `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access on a pool. A pool admin who deploys it intends to limit trading to a curated set of addresses (e.g., specific market makers, institutional counterparties, or KYC-verified wallets). Once the router is allowlisted — which is the only way to let any of those approved users trade through the standard periphery — the gate is silently open to the entire public. Any user can execute swaps against the pool's liquidity, consuming LP capital at oracle-derived prices that were calibrated for a restricted counterparty set. This constitutes a direct loss of LP principal and owed fees whenever the pool's pricing or depth assumptions depend on the restricted-access invariant.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is not a hypothetical configuration: any pool that uses `SwapAllowlistExtension` and also wants approved users to trade through the standard periphery **must** allowlist the router, because the router is the only supported multi-hop and exact-output path. The bypass is therefore reachable in every production deployment where the allowlist is intended to coexist with router-based trading. The attacker needs no special privilege — only the ability to call a public router function.

---

### Recommendation

The extension must resolve the actual user identity rather than the direct caller of `pool.swap`. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Check `recipient` instead of `sender` for the user-facing identity**, or add a dedicated `originalSender` field to the `beforeSwap` interface so the pool can forward the true initiator.

3. **Do not allowlist the router as a single address**: Instead, require the router to forward the real user identity in a verifiable way (e.g., a signed payload or a transient-storage context the router sets before calling the pool).

The core issue is that `sender` in the extension interface is the direct caller of `pool.swap`, not the economic actor initiating the trade. The allowlist must gate the economic actor.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted
  - Pool admin calls setAllowedToSwap(pool, alice, false)   // alice explicitly excluded

Attack:
  - alice calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ..., extensionData)
    → pool.msg.sender = router
    → _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; alice receives output tokens

Result:
  - alice, who is explicitly excluded from the allowlist, successfully swaps
  - the allowlist invariant is broken; any user can trade by routing through the public router
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
