### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged caller to bypass per-user swap allowlists via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the end user. A pool admin who adds the router to the allowlist (to let legitimate users access the pool via the router) inadvertently opens the pool to every user who can call the router, defeating the per-user gate entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct pool caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

This creates an irresolvable dilemma for any pool admin who deploys a swap-allowlisted pool:

| Admin action | Effect |
|---|---|
| Add only end-user addresses | Allowlisted users **cannot** use the router (router is not in the list → `NotAllowedToSwap`) |
| Add the router address | **Every** user who calls the router bypasses the per-user gate |

There is no configuration that simultaneously allows specific users to swap through the router while blocking others.

---

### Impact Explanation

When a pool admin adds the router to the allowlist so that legitimate users can access the pool via the router, any address — including addresses the admin explicitly never allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) and execute swaps against the pool. The extension's `allowedSwapper[pool][router]` check passes for every caller because the router is a single shared contract.

Concrete consequences:
- Unauthorized counterparties trade against LP positions, extracting value the pool was designed to deny them.
- Regulatory or compliance-gated pools (e.g., permissioned market-making pools) are silently open to the public.
- Any stop-loss or velocity guard layered on top of the allowlist still fires, but the first-line identity gate — the one that was supposed to restrict *who* can trade — is nullified.

---

### Likelihood Explanation

High. The router is a public, permissionless contract. The bypass requires only a standard `exactInputSingle` call with the target pool address. Pool admins who want their allowlisted users to enjoy the router's slippage protection and multi-hop routing will naturally add the router to the allowlist, triggering the vulnerability without any awareness that they have opened the pool to all callers.

---

### Recommendation

**Short term:** Document explicitly that adding the router to the allowlist opens the pool to all router users. Provide a per-user forwarding mechanism: the router should encode `msg.sender` in `extensionData`, and a new `RouterAwareSwapAllowlistExtension` should decode and check that value (trusting only calls from a known, factory-registered router).

**Long term:** Redesign the `sender` semantics so that the pool (or the extension framework) can distinguish the economic actor from the technical caller. One approach: the pool stores the original `tx.origin` or a router-supplied "on-behalf-of" address in transient storage and passes it as a separate `originator` field to extension hooks, so allowlist extensions can gate the true end user regardless of routing depth.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension; pool admin calls:
     swapExtension.setAllowedToSwap(pool, alice, true);
     swapExtension.setAllowedToSwap(pool, router, true);   // ← to let alice use the router
   charlie is NOT in the allowlist.

Attack
──────
2. charlie calls:
     router.exactInputSingle(ExactInputSingleParams({
         pool:           <allowlisted pool>,
         tokenIn:        token0,
         recipient:      charlie,
         amountIn:       X,
         amountOutMinimum: 0,
         zeroForOne:     true,
         priceLimitX64:  type(uint128).max,
         extensionData:  ""
     }));

3. router calls pool.swap(charlie, true, X, ...).
   pool.msg.sender == router → _beforeSwap(router, ...).
   Extension checks allowedSwapper[pool][router] → true → no revert.

4. charlie's swap executes and settles. The per-user allowlist is bypassed.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, where `sender` is the router address rather than the end user, and in `MetricOmmPool.swap` at line 231, where `msg.sender` (the router) is unconditionally forwarded as the identity the extension must gate. [6](#0-5) [7](#0-6)

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
