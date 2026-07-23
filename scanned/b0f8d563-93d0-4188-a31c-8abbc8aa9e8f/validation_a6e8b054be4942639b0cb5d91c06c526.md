### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` at the pool boundary, so the extension checks whether the **router contract** is allowlisted rather than the actual end user. If the pool admin allowlists the router (a necessary step to let allowlisted users use the router), every non-allowlisted user can bypass the swap gate by routing through the same public router.

---

### Finding Description

**Pool's `swap` passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that `sender` value and forwards it to every configured extension: [2](#0-1) 

**Extension checks the forwarded `sender`, not the real end user:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]` (pool → sender): [3](#0-2) 

**Router is `msg.sender` at the pool boundary:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool therefore sees `msg.sender = router`, and the extension receives `sender = router`: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The identity mismatch:**

| Entry path | `sender` seen by extension | Allowlist check |
|---|---|---|
| User calls `pool.swap` directly | user's EOA | `allowedSwapper[pool][user]` ✓ |
| User calls `router.exactInputSingle` | router contract | `allowedSwapper[pool][router]` ✗ |

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or a private market) faces an irreconcilable dilemma:

1. **If the router is NOT allowlisted:** Allowlisted users cannot use `MetricOmmSimpleRouter` at all (every router-mediated swap reverts with `NotAllowedToSwap`). The supported periphery path is broken for legitimate users.
2. **If the router IS allowlisted** (the only way to restore router access for legitimate users): The allowlist is completely bypassed. Any non-allowlisted address can call `router.exactInputSingle` and swap on the curated pool. The extension sees `sender = router` and passes the check unconditionally.

In scenario 2, the curated pool's swap gate is rendered inoperative for all router-mediated flows. Non-allowlisted users can drain liquidity, execute trades the pool admin intended to block, and violate any regulatory or business-logic constraints the allowlist was meant to enforce. This is a direct broken-core-functionality impact on curated pools.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point in the periphery. Any pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address. The mismatch between the checked identity (router) and the intended gated identity (end user) is not surfaced by any existing guard or documentation warning. The trigger is a single, reasonable admin action.

---

### Recommendation

The extension must check the **original end user**, not the intermediate router. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already tracks the real payer in transient storage (`_getPayer()`). Extend the `extensionData` or a dedicated transient slot to carry the original `msg.sender` and have the pool forward it as a separate `originator` argument to extensions.

2. **Alternatively, gate on `recipient` or require the pool to expose the original caller.** The pool could accept an explicit `swapper` parameter (verified against `msg.sender` or a signed permit) so the extension always sees the real economic actor regardless of routing depth.

Until fixed, pool admins should be warned that allowlisting the router address opens the pool to all users, and that direct-pool-only access is the only safe configuration for the current extension design.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, zeroForOne, amount, ...)
  - pool calls _beforeSwap(msg.sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true  →  PASSES
  - bob's swap executes on the curated pool despite not being allowlisted

Result:
  - SwapAllowlistExtension is completely bypassed for all router-mediated swaps
  - Any non-allowlisted user can trade on the curated pool
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
