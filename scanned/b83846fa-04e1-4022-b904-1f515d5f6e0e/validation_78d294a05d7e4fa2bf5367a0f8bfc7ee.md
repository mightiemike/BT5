### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. That argument is the pool's own `msg.sender` — the router contract — not the originating user. If the pool admin allowlists the router to enable router-mediated swaps, every non-allowlisted address can bypass the individual swap gate by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain that exposes the bug:**

`MetricOmmSimpleRouter.exactInputSingle` → `IMetricOmmPoolActions(pool).swap(...)` → `MetricOmmPool._beforeSwap(msg.sender, ...)` → `ExtensionCalling._callExtensionsInOrder` → `SwapAllowlistExtension.beforeSwap(sender, ...)`

In `MetricOmmPool.swap`, the first argument forwarded to `_beforeSwap` is `msg.sender` — the address that called the pool: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that value verbatim as the `sender` parameter to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, i.e. `allowedSwapper[pool][router]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the pool's `msg.sender`: [4](#0-3) 

So the allowlist check resolves to `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`. The individual user identity is never consulted.

**The dilemma this creates for the pool admin:**

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every user — including non-allowlisted ones — can bypass the gate via the router |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

There is no configuration that simultaneously enforces per-user allowlisting and permits router-mediated swaps.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, regulatory-restricted) with `SwapAllowlistExtension` and allowlists the router to support normal periphery usage inadvertently opens the pool to every address. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, the extension sees `sender = router`, the check passes, and the swap executes. The allowlist provides zero protection against router-mediated access. This is a direct, fund-impacting policy bypass: tokens flow through a pool that was intended to be restricted.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected configuration step for any pool that intends to support the standard periphery. The `MetricOmmSimpleRouter` is the primary user-facing swap entry point; a pool admin who does not allowlist it effectively makes the pool unusable for normal users. The configuration that triggers the bypass is therefore the default operational choice, not an exotic edge case.

---

### Recommendation

The extension must check the originating user, not the intermediary. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention.
2. **Add a dedicated `swapper` field to the hook signature**: Separate the "caller of the pool" (`sender`) from the "economic actor" (`swapper`) in the `beforeSwap` interface, and have the router populate the latter with its own `msg.sender`.

Until fixed, pools that require per-user swap gating must not allowlist the router and must instruct users to call the pool directly.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin allowlists Alice (KYC'd) and the router address
  Bob (not allowlisted) holds token0

Attack:
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    msg.sender = router
  Pool calls _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  Swap executes; Bob receives token1

Result:
  Bob, who is not individually allowlisted, successfully swaps in the curated pool.
  The allowlist is completely bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
