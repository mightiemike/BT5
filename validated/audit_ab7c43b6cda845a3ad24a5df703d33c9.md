Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on router address instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address. Any pool admin who allowlists the router to enable router-based swaps inadvertently grants every user the ability to bypass per-address restrictions, rendering the allowlist ineffective for all router-mediated swap flows.

## Finding Description

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling.sol` forwards this `sender` directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on this `sender`: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

So `sender` received by the extension is the router address, not the actual user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same applies to multi-hop `exactInput` (line 103-112) and `exactOutput` (line 220-228) paths, where the router calls `pool.swap` for each hop. [5](#0-4) 

For a curated pool to support router-based swaps at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — regardless of individual allowlist status — can bypass the restriction by routing through `MetricOmmSimpleRouter`. The existing test suite only tests direct pool calls (where `msg.sender` is the actual swapper), not router-mediated calls, so this bypass is untested. [6](#0-5) 

## Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the router is added to the allowlist. The allowlist — the sole access-control mechanism for curated pools — is rendered ineffective for all router-based swap flows. Pools designed to restrict swap access to specific counterparties (e.g., institutional, KYC-gated, or regulatory-compliant pools) lose that protection entirely on the primary supported periphery path. This constitutes broken core pool functionality and an admin-boundary break where an unprivileged user bypasses a configured access control gate.

## Likelihood Explanation

Medium. The trigger is the pool admin adding the router to the allowlist, which is the natural and expected configuration for any curated pool that also wants to support the protocol's own router. There is no way to simultaneously allow router-based swaps and enforce per-user restrictions with the current extension design, so any pool admin who attempts to do so will unknowingly open the bypass. No special attacker capability is required beyond calling the public router.

## Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension, and the extension must verify the `extensionData` origin is trustworthy (e.g., only when `sender` is a known router).
2. **Dedicated router-aware check:** Maintain a registry of trusted routers in the extension; when `sender` is a known router, decode and verify the actual user from `extensionData` rather than accepting the router address as the gated identity.

## Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // Alice is the only allowed swapper
3. Pool admin: setAllowedToSwap(pool, router, true)  // Router allowlisted to enable router-based swaps
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — pool's msg.sender = router
6. beforeSwap receives sender = router; checks allowedSwapper[pool][router] == true → passes
7. Bob's swap executes despite not being individually allowlisted
8. Repeat for exactInput and exactOutput multi-hop paths
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
