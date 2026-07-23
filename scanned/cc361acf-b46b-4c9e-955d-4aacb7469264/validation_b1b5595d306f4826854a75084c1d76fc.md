### Title
`SwapAllowlistExtension` checks the router's address as the swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value the pool passes as its first argument — which is always `msg.sender` of the pool's own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool boundary, so the extension checks whether the **router** is allowlisted, not the actual user. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user on-chain.

---

### Finding Description

**Actor binding in the pool**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Extension check**

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., the immediate caller of `pool.swap()`. [2](#0-1) 

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The router is `msg.sender` at the pool boundary, so `sender` forwarded to the extension is the **router address**, not the originating EOA. [3](#0-2) 

The same substitution occurs for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (outer hop). For inner hops of `exactOutput`, `sender` becomes the **previous pool's address** — an even more unexpected identity. [4](#0-3) 

**Consequence**

A pool admin who wants allowlisted users to be able to trade through the router must add the router to `allowedSwapper`. The moment the router is allowlisted, every user on-chain can call `exactInputSingle` (or any other router entry point) and the extension will see `allowedSwapper[pool][router] == true` — the per-user allowlist is completely bypassed.

Conversely, if the admin does *not* allowlist the router, legitimately allowlisted users cannot use the router at all — their swaps revert with `NotAllowedToSwap` because the extension sees the router address, which is not in the allowlist.

---

### Impact Explanation

**Allowlist bypass (critical path):** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses can be fully bypassed by any EOA routing through `MetricOmmSimpleRouter`. The attacker receives pool output tokens and the pool's LP providers bear the full economic exposure of an unrestricted swap — direct loss of the protection the allowlist was deployed to enforce.

**Broken core functionality (secondary path):** Even without the bypass, allowlisted users who attempt to trade through the router are incorrectly rejected, making the router unusable on any allowlisted pool unless the admin sacrifices the allowlist's integrity.

Both outcomes satisfy the allowed impact gate: broken core pool functionality causing loss of funds or unusable swap flows, and admin-boundary break where an unprivileged path bypasses a factory/pool role check.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension` and also wants their allowlisted users to benefit from slippage protection, multi-hop routing, or deadline enforcement will naturally allowlist the router. There is no documentation warning against this. The bypass requires no special privilege — any EOA with a standard ERC-20 approval can exploit it.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Trusted-forwarder pattern in the router**: Before calling `pool.swap`, the router writes the originating `msg.sender` into a transient storage slot. The extension reads that slot (via a known interface on the router) when `sender` equals the router address, and checks the originating user instead.

2. **Extension-data forwarding**: The router encodes the originating `msg.sender` into `extensionData`. The extension decodes it only when `sender` is a known, factory-registered router, preventing spoofing by arbitrary callers. The factory would need a router registry.

Either way, the extension must never treat the router address as the economically relevant actor for allowlist purposes.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension
  admin allowlists: Alice (EOA), router (MetricOmmSimpleRouter)
  Bob   = non-allowlisted EOA

Attack:
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
        msg.sender in pool = router
        pool calls _beforeSwap(router, ...)
            SwapAllowlistExtension.beforeSwap(sender=router, ...)
                allowedSwapper[pool][router] == true  ← passes
    ← swap executes; Bob receives output tokens

Result:
  Bob, who is not in the allowlist, successfully swaps on a curated pool.
  Alice's allowlist entry is irrelevant — the router entry is the effective gate.
```

The existing unit tests in `SwapAllowlistSubExtension.t.sol` call the extension directly with `vm.prank(address(pool))` and pass the user address as `sender` — they never exercise the router path and therefore do not catch this binding error. [5](#0-4)

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
