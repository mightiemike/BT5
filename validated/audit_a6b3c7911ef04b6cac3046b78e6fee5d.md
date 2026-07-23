Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing full per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, the router is the direct caller, so the check resolves to `allowedSwapper[pool][router]` — the router's allowlist status — not the actual user's. Any unpermissioned user who routes through the public router bypasses the per-user allowlist entirely if the router is allowlisted, which is the natural operational configuration to support normal usage.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` — in every case the router is `msg.sender` on the pool: [5](#0-4) 

The allowlist lookup becomes `allowedSwapper[pool][router]`. There is no configuration that simultaneously enforces per-user allowlisting and permits router-mediated swaps: allowlisting the router opens the gate to all users; not allowlisting it blocks all router-mediated swaps including those from legitimately allowlisted users.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that protection entirely for any user routing through `MetricOmmSimpleRouter`. The pool executes swaps at oracle prices, so LPs bear full economic exposure to the unrestricted counterparty — including adverse-selection or regulatory risk the allowlist was designed to prevent. This is a direct, fund-impacting loss of the policy invariant the extension was configured to enforce, qualifying as a broken core pool access-control mechanism with material LP impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary production swap entry point. No privileged access, special token, or malicious setup is required — only knowledge that the pool uses `SwapAllowlistExtension` and that the router is allowlisted (or that the admin will allowlist it to support normal usage). The bypass is reachable on every live curated pool that uses the router, by any on-chain caller, at zero extra cost beyond gas.

## Recommendation
The extension must gate the economic actor, not the immediate pool caller. Two sound approaches:

1. **Pass the originating user through `extensionData`.** The router populates a `swapper` field in `extensionData` with `msg.sender` before calling the pool; the extension decodes and verifies it. The pool's `sender` field remains the router, but the extension reads the real user from the payload.
2. **Add an explicit `originator` parameter to the swap interface** so the pool can pass the true initiating address to extensions independently of the callback payer.

Either way, `SwapAllowlistExtension.beforeSwap` must not rely on `sender` alone when a public intermediary contract is a supported entry point.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin: allowedSwapper[pool][alice] = true
  admin: allowedSwapper[pool][router] = true  ← natural step so Alice can use the router

Attack (Mallory, not allowlisted):
  1. Mallory calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=Mallory, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; Mallory receives output tokens

Result:
  allowedSwapper[pool][mallory] == false, yet Mallory's swap succeeds.
  The per-user allowlist is fully bypassed for any user routing through
  the public MetricOmmSimpleRouter.
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
