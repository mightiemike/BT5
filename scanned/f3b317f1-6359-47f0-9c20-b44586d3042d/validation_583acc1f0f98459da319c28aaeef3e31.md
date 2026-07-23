### Title
SwapAllowlistExtension Bypass via Router: Wrong Identity Checked Allows Non-Allowlisted Users to Swap - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` of the pool call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the swap gate by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract: [4](#0-3) 

So the extension checks `allowedSwapper[pool][router]` — the router's allowlist status — not the end user's. A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router address. Once the router is allowlisted, the check passes for every caller regardless of their individual allowlist status, because the router is a public, permissionless contract that any address can call.

### Impact Explanation

Any non-allowlisted user can bypass the `SwapAllowlistExtension` gate on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool receives and settles the swap normally; the extension never sees the real user's address. This directly breaks the core access-control invariant the extension is designed to enforce, allowing unrestricted swap access to a pool that was configured to be permissioned. Depending on the pool's purpose (e.g., institutional-only liquidity, regulatory-gated pools), this constitutes a direct loss of the access-control guarantee and enables unauthorized fund flows through the pool.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural operational step when the pool is meant to be accessible via the standard periphery. Any user who discovers the router is allowlisted can immediately exploit the bypass with a single `exactInputSingle` call. No privileged access, no special tokens, and no malicious setup are required beyond the router being allowlisted.

### Recommendation

The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the real `msg.sender` (the end user) into `extensionData` so extensions can decode and check it. The extension interface already passes `extensionData` through to every hook.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode a trusted-caller-supplied user address from `extensionData` when `sender` is a known periphery contract, or the pool should expose a dedicated "originator" field that periphery contracts are required to populate.

Until fixed, pool admins should **not** allowlist the router address on permissioned pools; instead, allowlisted users must call the pool directly.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // admin allowlists router to support alice's router usage

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
      → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
          msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes normally

Result: bob swaps on a pool he is not allowlisted for.
```

The wrong identity — the router — is checked instead of the actual swapper, directly mirroring the external bug's pattern of using a pre-transformation value where the post-transformation value is required. [5](#0-4) [6](#0-5) [7](#0-6)

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
