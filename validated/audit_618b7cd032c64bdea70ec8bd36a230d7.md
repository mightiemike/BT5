### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The allowlist therefore gates on the router address. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those the admin intended to block — can bypass the restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user can bypass the allowlist via the router |
| Allowlist individual users only | Those users cannot use the router at all |

There is no configuration that simultaneously (a) allows router-mediated swaps and (b) restricts which end-users may swap.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict which addresses may trade against its liquidity. With the router bypass, any user can execute swaps against a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput`. LP funds in the restricted pool are exposed to the full universe of swappers, defeating the allowlist entirely. This is a direct loss-of-access-control over LP assets.

---

### Likelihood Explanation

No special conditions are required. Any user who is blocked by the allowlist on a direct `pool.swap()` call can immediately retry through the router with identical parameters. The router is a public, permissionless contract. Likelihood is high.

---

### Recommendation

The extension must check the identity of the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the real payer through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. This requires a convention between router and extension.

2. **Check `sender` only for direct pool calls; require router to be unlisted**: Document that pools using `SwapAllowlistExtension` must not allowlist any router or intermediary, and that users must call the pool directly. This is a severe UX restriction but preserves the guard's correctness.

The root fix is that `SwapAllowlistExtension.beforeSwap()` must receive and verify the end-user identity, not the intermediary's address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists the router (required for router-mediated swaps to work)
  - Pool admin does NOT allowlist Alice

Attack:
  1. Alice calls pool.swap() directly → reverts NotAllowedToSwap ✓ (guard works)
  2. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...) 
     → router calls pool.swap() with msg.sender = router
     → extension checks allowedSwapper[pool][router] → true
     → swap executes ✗ (guard bypassed)

Result: Alice swaps against a pool she was explicitly blocked from, 
        extracting value from LPs who expected a restricted pool.
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
