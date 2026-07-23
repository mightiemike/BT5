### Title
`SwapAllowlistExtension` gates the router address instead of the actual end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the **router**, not the actual user. Any pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` of the pool is the **router contract**, so `sender` delivered to the extension is the router address — not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`, which is identical for every user who routes through the same router instance.

The same substitution occurs for every hop in `exactInput` (where the payer context correctly tracks `msg.sender` for payment, but the `sender` seen by the extension is still the router): [5](#0-4) 

---

### Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

1. **Router not allowlisted**: allowlisted users cannot use `MetricOmmSimpleRouter` at all — their swaps revert at the extension even though they are individually permitted. Core swap functionality is broken for the intended users.
2. **Router allowlisted**: every user on the network can bypass the per-user allowlist by routing through the router. The curation policy is completely nullified.

In scenario 2, unauthorized users gain unrestricted swap access to a pool that was designed to be curated. Depending on pool configuration, this enables unauthorized price impact, fee extraction, or interaction with stop-loss and velocity guards in ways the pool admin did not intend — all of which can cause direct LP principal loss.

---

### Likelihood Explanation

Supporting router-mediated swaps is the normal, expected use of the periphery. A pool admin who deploys a curated pool and wants their allowlisted users to benefit from multi-hop routing or WETH unwrapping will naturally allowlist the router. The documentation and interface give no indication that doing so opens the pool to all users. The trigger requires no special privilege — any user can call `exactInputSingle` or `exactInput` on the router.

---

### Recommendation

The extension must receive the **original end user** rather than the immediate pool caller. Two complementary fixes:

1. **Pool-side**: pass the original initiator through a separate field (e.g., a dedicated `originator` argument in the extension interface, populated from transient storage set at the router entry point).
2. **Extension-side (short-term)**: document that `SwapAllowlistExtension` is incompatible with router-mediated flows and revert in `beforeSwap` when `sender` is a known router, or require pools using this extension to be accessed only via direct `pool.swap()` calls.

The cleanest fix is to store the original `msg.sender` in transient storage at the router entry point and expose it to the pool so it can be forwarded as a verified `originator` to extensions, analogous to how Uniswap v4 separates `sender` from `hookData` origin.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted to support alice's router swaps

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(...) — msg.sender of pool = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - bob's swap executes on the curated pool despite not being allowlisted

Result:
  - SwapAllowlistExtension's per-user curation is completely bypassed
  - Any user can swap on the curated pool via the router
``` [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
