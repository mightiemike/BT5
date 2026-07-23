### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps, every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the public router.

### Finding Description

**Call chain:**

```
User (non-allowlisted) → router.exactInputSingle(...)
  → pool.swap(recipient, ...)          // msg.sender = router
    → _beforeSwap(msg.sender=router, ...)
      → SwapAllowlistExtension.beforeSwap(sender=router, ...)
        → allowedSwapper[pool][router]  // checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the router is the caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` at the pool level: [4](#0-3) 

The router stores the original user only in transient callback context for payment purposes; it is never forwarded to the pool as the swap `sender`. [5](#0-4) 

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool and then allowlists the router address (the natural step to enable router-mediated swaps for their allowlisted users) inadvertently opens the gate to **every user on the network**. Any address can call `router.exactInputSingle / exactInput / exactOutputSingle / exactOutput` and the extension will see `sender = router_address`, which is allowlisted, and permit the swap. The per-user allowlist is completely nullified for all router-mediated paths. This constitutes a curation failure: non-KYC'd, non-institutional, or otherwise excluded users can trade on a pool that was explicitly configured to block them.

### Likelihood Explanation

The `SwapAllowlistExtension` is the protocol's production swap-gating primitive. Its primary use case is curated pools (institutional, KYC, whitelist-only). The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. A pool admin who wants allowlisted users to be able to use the router **must** allowlist the router address — there is no other mechanism. The moment they do so, the bypass is live and requires no further action from an attacker beyond calling the public router. The `generate_scanned_questions.py` research file explicitly flags this exact vector: [6](#0-5) 

### Recommendation

Pass the **original initiating user** through the swap path so the extension can gate on the economically relevant actor. Two complementary fixes:

1. **Router-side:** Store the original `msg.sender` in transient context and pass it as a dedicated field in `callbackData` or `extensionData` so extensions can decode it.
2. **Extension-side:** `SwapAllowlistExtension.beforeSwap` should decode the original user from `extensionData` (if present) and fall back to `sender` only for direct pool calls. Alternatively, the extension can reject any `sender` that is a known router/intermediary unless the original user is also allowlisted.

The cleanest fix is for the pool to accept an explicit `originator` parameter in `swap()` that is set to `msg.sender` for direct calls and to the user for router calls, then pass that to extensions as the authoritative identity.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router to enable router-mediated swaps.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // natural admin step

// Attack: bannedUser bypasses the allowlist via the router.
vm.prank(bannedUser);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       bannedUser,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Swap succeeds: extension saw sender=router (allowlisted), never checked bannedUser.
// bannedUser receives token1 output despite being explicitly excluded.
```

The extension checks `allowedSwapper[pool][router]` (true) and never inspects `bannedUser`. [7](#0-6) [8](#0-7)

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
