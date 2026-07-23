### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the address the pool passes as `msg.sender` of its own `swap` call — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the natural admin action to let allowlisted users use the router), every unprivileged user can bypass the swap gate by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The pool therefore passes the router address as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

- **Router not allowlisted**: every allowlisted user is blocked from using the router — core swap functionality is broken for the intended audience.
- **Router allowlisted** (the natural fix): `allowedSwapper[pool][router] == true`, so the guard passes for every caller regardless of whether the actual end user is on the allowlist. Any unprivileged address can bypass the swap gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router.

The analog to the Queue `remove` bug is exact: the allowlist node for the router is "present" in the mapping (like the undeleted queue node), so the `contains` check (`allowedSwapper[pool][router]`) returns `true` for every user who routes through it, even though those users were never individually authorized.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-gated, institutional-only, or regulatory-compliant participants) can be freely traded by any address via the public router. The pool's access-control invariant is silently voided. Depending on pool design, this can allow unauthorized parties to extract liquidity at privileged pricing, drain one-sided bins, or interact with pools that were contractually restricted to specific counterparties — all constituting direct loss of LP assets or unauthorized fund flows above Sherlock thresholds.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap interface. Any pool admin who enables `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router must allowlist the router address. This is the obvious and expected configuration. The bypass is therefore reachable by any unprivileged user on every such pool with no special preconditions.

### Recommendation

The extension must gate on the actual end user, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: require the router to encode the real user address in `extensionData` and have the extension decode and verify it (with a pool-level flag requiring this encoding so it cannot be omitted).
2. **Check `recipient` instead of `sender`**: for swap allowlists the economically relevant actor is the recipient of output tokens; the extension could check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`, though this still requires the router to set `recipient` to the actual user (which it already does via `params.recipient`).

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use it.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: bob, ...}).
5. Router calls pool.swap(bob, ...) → pool passes msg.sender=router to _beforeSwap.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes successfully despite never being allowlisted.
``` [3](#0-2) [1](#0-0) [5](#0-4)

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
