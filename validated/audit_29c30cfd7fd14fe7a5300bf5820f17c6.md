Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, the router is `msg.sender` at the pool, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to support normal UX simultaneously opens the pool to every on-chain user, rendering the per-user allowlist inoperative.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no `swapper` forwarding, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension therefore checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`. No existing guard corrects this: the pool has no mechanism to pass the original end-user address through the call stack, and the extension has no fallback check on the true economic actor.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (KYC-verified counterparties, whitelisted market makers, private-pool participants) loses that restriction entirely for any user who calls through the router. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the allowlist was designed to prevent. This constitutes a direct loss of LP principal and a broken core pool invariant (admin-boundary break by an unprivileged path).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard documented periphery entry point for swaps. Pool admins who deploy a restricted pool and want to support normal UX must allowlist the router. Once they do, the bypass is trivially reachable by any unprivileged user with zero additional preconditions — no special role, no capital beyond the swap input, and no complex setup.

## Recommendation
Pass the original end-user address through the swap path so the extension can gate on the actual economic actor. The preferred fix is to add an explicit `swapper` parameter to `pool.swap()` that callers supply. The pool passes it to `_beforeSwap` instead of `msg.sender`. The router sets `swapper = msg.sender` (the user who called the router). The extension checks `allowedSwapper[pool][swapper]`. An alternative is to have the router store the original caller in transient storage and expose a view that the extension reads during `beforeSwap`, but this adds complexity without changing the core interface.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the only allowed user)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)   [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result: bob bypasses the allowlist and swaps successfully.
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
