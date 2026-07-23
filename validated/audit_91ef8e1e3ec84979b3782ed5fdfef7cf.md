Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual swapper, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When any user calls `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every non-allowlisted user can bypass the gate by routing through the router, completely defeating the allowlist's purpose.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook chain:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230–231. [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:**

`_callExtensionsInOrder` encodes `sender` directly into the `beforeSwap` call at lines 162–165. [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`:**

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`. The check at line 37 evaluates `allowedSwapper[pool][router]` when the router is the caller, not `allowedSwapper[pool][actualUser]`. [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:**

The router calls `IMetricOmmPoolActions(params.pool).swap(...)` at line 72, making the pool see `msg.sender == router`. The actual end user's address is never forwarded to the pool or extension. [4](#0-3) 

The same identity collapse occurs in `exactInput` (intermediate hops use `address(this)` as payer) and in the `_exactOutputIterateCallback` recursive path which also calls `pool.swap()` from the router context. [5](#0-4) [6](#0-5) 

**Root cause:** The `sender` field in the hook chain represents the immediate caller of `pool.swap()`, not the economic actor. There is no mechanism to propagate the original user's address through the router into the extension. The pool admin faces an impossible choice: not allowlisting the router blocks all allowlisted users from using the router; allowlisting the router opens the gate to every user.

## Impact Explanation

Any address not on the allowlist can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`/`exactOutputSingle`). The pool admin's intent to restrict swap access to specific addresses is completely defeated. Unauthorized swaps move the pool's bin cursor, consume LP liquidity, and extract output tokens — constituting direct loss of LP assets and broken core pool swap-gating functionality. This matches the allowed impact category: broken core pool functionality causing loss of funds and unusable swap access controls.

## Likelihood Explanation

The bypass is reachable by any unprivileged user with zero special permissions. The only prerequisite is that the pool admin allowlists the router, which is the natural and expected action for any pool intended to be usable through the protocol's own periphery router. The incentive to allowlist the router is strong (otherwise allowlisted users cannot use the router at all), making the bypass highly likely to be reachable in any real deployment. The attack requires only a standard router call with valid swap parameters.

## Recommendation

The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract:

1. **Router-side fix**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (e.g., as a leading ABI-encoded address) so extensions can decode and verify the real user.
2. **Extension-side fix**: `SwapAllowlistExtension.beforeSwap` should decode the real originator from `extensionData` when `sender` is a known/trusted router, or the pool interface should carry an explicit `originator` field through the hook chain.
3. Until fixed, pool admins must be warned that allowlisting the router is functionally equivalent to calling `setAllowAllSwappers(pool, true)`.

## Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER.
2. Pool admin calls:
     extension.setAllowedToSwap(pool, alice, true);   // allowlist Alice
     extension.setAllowedToSwap(pool, router, true);  // allow router so Alice can use it
3. Bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Inside pool.swap(), msg.sender == router (not Bob).
5. Extension checks: allowedSwapper[pool][router] == true → passes.
6. Bob's swap executes on the restricted pool, extracting output tokens.
7. Bob's direct call (pool.swap() with msg.sender == bob) would revert:
     allowedSwapper[pool][bob] == false → NotAllowedToSwap.
```

The same bypass works via `exactInput`, `exactOutputSingle`, and `exactOutput`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
