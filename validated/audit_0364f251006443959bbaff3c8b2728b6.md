### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router to support normal periphery usage simultaneously grants every unprivileged user the ability to bypass the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` stores the original user in transient storage for payment purposes only, then calls `pool.swap()` directly — making the router the `msg.sender` the pool sees: [4](#0-3) 

The original user's address (`msg.sender` of `exactInputSingle`) is never forwarded to the pool or to any extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

This creates an irreconcilable dilemma for any pool admin who deploys a pool with `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: even explicitly allowlisted users cannot swap through the router; the extension reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router]` is false.
- **If the router IS allowlisted**: every unprivileged user can call `router.exactInputSingle(pool, ...)` and the extension passes unconditionally, because `allowedSwapper[pool][router]` is true regardless of who the end user is.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user can execute swaps on a pool that the admin intended to restrict to a curated set of addresses. On a KYC-gated, institutional, or otherwise curated pool, this allows unauthorized parties to trade against LP liquidity, extract value at oracle-derived prices, and violate the pool's access policy. The LP funds are directly at risk because the pool will settle real token transfers for every bypass swap.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, production-deployed periphery entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which is the normal operational expectation. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle`.

---

### Recommendation

The extension must recover the original end-user identity rather than trusting the immediate `pool.swap()` caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: the router encodes `msg.sender` into `extensionData` for each hop, and the extension verifies a signed or router-attested identity from that payload. This requires a trusted router registry in the extension.

2. **Check `sender` only when `sender` is not a known router**: the extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the real user from a standardized field in `extensionData` and checks that address instead.

Either way, the invariant to enforce is: the allowlist must gate the address that economically initiates and pays for the swap, not the intermediate contract that relays it.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Deploy pool with ext as beforeSwap extension
MetricOmmPool pool = factory.deployPool(..., ext, ...);

// Admin allowlists the router so that allowed users can trade normally
ext.setAllowedToSwap(address(pool), address(router), true);
// Admin also allowlists alice
ext.setAllowedToSwap(address(pool), alice, true);

// Eve is NOT allowlisted
// Eve calls the router directly — extension sees sender=router, which IS allowlisted
vm.prank(eve);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: eve,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// ✓ swap succeeds — eve bypassed the allowlist
```

The `beforeSwap` hook receives `sender = address(router)`, which is in `allowedSwapper[pool]`, so the guard passes and Eve's swap executes against LP funds. [6](#0-5) [7](#0-6) [8](#0-7)

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
