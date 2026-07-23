Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router contract is the direct caller, so `sender` equals the router address rather than the end user. A pool admin who allowlists the router to restore router usability for their curated users inadvertently grants every address on the network the ability to bypass the allowlist, exposing LP providers to trades from non-vetted counterparties.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

All four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) call `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

Because the extension sees `sender = router_address`, the allowlist check is keyed to the router, not the end user. The pool admin faces two equally broken outcomes:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use `MetricOmmSimpleRouter` — the hook reverts with `NotAllowedToSwap` for every router-mediated call |
| Yes | Every address on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter` |

No existing guard in `SwapAllowlistExtension`, `ExtensionCalling`, or `MetricOmmPool` checks the originating user identity when the direct caller is a router. The `extensionData` field is passed through but never decoded or enforced by the extension.

## Impact Explanation
When the pool admin allowlists the router to restore router usability for their curated users, `SwapAllowlistExtension` becomes a no-op for any user who calls through the router. Non-allowlisted users can execute swaps against a pool whose LP providers deposited under the assumption that only vetted counterparties would trade against them. LP providers bear the full market risk of trades they never consented to, constituting a direct loss of LP principal relative to the curated-pool guarantee they relied on. This matches the allowed impact gate: broken core pool functionality causing loss of funds and direct loss of LP assets.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, production-deployed periphery swap path. Any pool admin who wants their allowlisted users to be able to use the standard router will naturally add the router to the allowlist via `setAllowedToSwap(pool, router, true)`. The mistake is non-obvious: the call reads as "allow the router," not "disable the allowlist for all users." No malicious intent is required — a single, reasonable admin call triggers the bypass. Any unprivileged user can then exploit it by calling any of the four router swap functions targeting the affected pool.

## Recommendation
The extension must gate on the actual end user, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and checks that address. This requires a coordinated change to the router and the extension.
2. **Trusted router registry**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the actual user address to be supplied and verified via `extensionData`.

The invariant that must hold is: the address checked against `allowedSwapper` must be the economic actor who benefits from the swap output, regardless of which supported periphery path was used.

## Proof of Concept
```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to let Alice use MetricOmmSimpleRouter.
4. Bob (not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — msg.sender seen by pool = router address.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes on the curated pool.
   Bob receives output tokens; LP providers bear the trade they never consented to.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
