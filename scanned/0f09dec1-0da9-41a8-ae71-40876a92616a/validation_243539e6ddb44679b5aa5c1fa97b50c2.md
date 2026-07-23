### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is the pool's `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user on earth, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point the pool's `msg.sender` is the **router address**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][end_user]`. The actual end user's identity is never consulted.

This produces two mutually exclusive failure modes:

**Mode A — Allowlist bypass (critical):** The pool admin allowlists the router address so that router-mediated swaps are permitted. Because the check is `allowedSwapper[pool][router]`, every user who calls the router passes the guard regardless of their own address. The per-user allowlist is completely defeated.

**Mode B — Broken core functionality (high):** The pool admin allowlists individual user addresses (alice, bob). Those users call the router. The extension sees `sender = router`, which is not in the allowlist, and reverts `NotAllowedToSwap`. Allowlisted users cannot use the primary swap entry point.

### Impact Explanation

**Mode A** is the fund-impacting path. A curated pool with a swap allowlist is typically deployed to restrict counterparties — e.g., a pool that should only trade against a specific market maker or KYC'd set of addresses. If the pool admin allowlists the router (the natural step to enable normal user access), every address on-chain can trade against the pool's LP positions. LPs suffer direct principal loss from trades they never consented to allow, and the pool's curation invariant is permanently broken for the lifetime of the pool.

**Mode B** breaks core swap functionality for allowlisted users who rely on the router, which is the protocol's primary user-facing entry point.

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Pool admins who want to deploy a curated pool while still allowing normal UX will naturally allowlist the router. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — any unprivileged address can exploit it by calling `exactInputSingle` on the router against the affected pool. The trigger is a single public transaction.

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor** (the end user), not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already knows `msg.sender` (the end user). It should encode the end user's address in `extensionData` and the extension should decode and check it. Alternatively, the pool could accept an explicit `swapper` parameter distinct from `msg.sender`.

2. **Document the limitation clearly.** Until a fix is deployed, the NatSpec on `SwapAllowlistExtension` must warn that allowlisting the router grants access to all router users, and that per-user gating requires direct pool calls only.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps)

Attack:
  - Attacker (address NOT in allowlist) calls:
      router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap() → pool's msg.sender = router
  - _beforeSwap(sender=router, ...) is dispatched
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Attacker's swap executes against LP positions
  - Allowlist provided zero protection
```

The root cause is identical to the external report's cross-chain address mismatch: both assume the direct address in the call frame (`user_` on L1 / `msg.sender` at the pool) is the same identity as the economic actor, but an intermediary (L1Sender / MetricOmmSimpleRouter) breaks that assumption. [5](#0-4) [6](#0-5) [7](#0-6)

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
