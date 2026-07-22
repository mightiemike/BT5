### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` passes `msg.sender` (the router contract address) as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the curated allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router contract**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap` directly, making the router the `sender` the extension sees. [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swapping to a specific set of addresses. To allow those addresses to also use the router (the standard periphery entry point), the admin must allowlist the router address. Once the router is allowlisted, **any** address on the network can call `exactInputSingle` or `exactInput` through the public router and the extension will approve the swap, because it sees `sender = router` (allowlisted) rather than the actual caller. The entire allowlist is silently bypassed for all router-mediated swaps. Unauthorized users can drain arbitrage value, front-run allowlisted LPs, or violate any access-control invariant the pool was designed to enforce.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery swap entry point. Any user can call it without any special privilege. The bypass requires no special setup beyond the pool admin having allowlisted the router — a necessary step for the extension to work correctly with the router at all. The condition is therefore reachable in every realistic curated-pool deployment that also supports router access.

### Recommendation

The extension must recover the **original user** rather than the immediate pool caller. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that value. This requires a convention between router and extension.
2. **Check `sender` against a router registry and, when `sender` is a known router, require the router to attest the real user**: The extension reads an attested-user field from `extensionData` only when `sender` is a trusted router address.

The simplest correct fix for direct-pool calls (no router) is already correct; the gap is exclusively the router-mediated path where `msg.sender` to the pool is not the economic actor.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap slot.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted
  - Pool admin does NOT allowlist attacker address.

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      <curated pool>,
      recipient: attacker,
      zeroForOne: true,
      amountIn:  X,
      ...
  });

Trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, ..., extensionData)
        msg.sender = router  ← pool records this as `sender`
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  ← PASSES
      → swap executes, attacker receives output tokens

Result:
  Attacker (not on allowlist) successfully swaps against the curated pool.
  The allowlist invariant is broken for every router-mediated call.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L100-112)
```text
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
