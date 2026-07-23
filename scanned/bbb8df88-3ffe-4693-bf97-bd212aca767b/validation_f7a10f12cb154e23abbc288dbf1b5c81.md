### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's direct `msg.sender`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (necessary for any allowlisted user to use the router), every user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` with `msg.sender = router`: [4](#0-3) 

The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an inescapable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users **cannot** use the router (blocked because `sender = router`) |
| Yes | **Any** user can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows specific users to use the router while blocking others.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to specific, trusted counterparties. LPs deposit funds expecting only those counterparties to trade against them. If the router is allowlisted (the only way to let legitimate users use the router), any unprivileged user can call `router.exactInputSingle` targeting the curated pool and trade successfully. This exposes LP funds to unauthorized counterparties who may time trades against stale oracle windows or otherwise extract value the LP did not consent to.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it with any pool address. The bypass requires only that the pool admin has allowlisted the router address — a natural step when setting up a curated pool whose legitimate users are expected to use the router. No privileged access, special tokens, or unusual conditions are required.

---

### Recommendation

The extension must check the economically relevant actor, not the pool's direct caller. Two sound approaches:

1. **Forward the original user via `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks that address instead of `sender`.
2. **Check `tx.origin` as a fallback identity**: Only viable if the protocol accepts the `tx.origin` trust model; not recommended for general use.
3. **Dedicated router allowlist**: Maintain a separate mapping `allowedRouterUser[pool][router][user]` and have the router attest the user identity in `extensionData`, with the extension verifying the attestation came from an allowlisted router.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is trusted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., amountIn: X, ...})
5. Router calls pool.swap(recipient=bob, ...) — msg.sender = router
6. Pool calls extension.beforeSwap(sender=router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes in the curated pool, bypassing the allowlist entirely.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, where `sender` (the pool's direct caller) is checked instead of the originating user's address. [5](#0-4)

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
