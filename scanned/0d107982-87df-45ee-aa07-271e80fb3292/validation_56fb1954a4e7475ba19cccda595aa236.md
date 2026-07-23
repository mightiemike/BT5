### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user, making per-user swap gating unenforceable for router-mediated swaps - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension` is designed to gate individual swappers on curated pools. However, when a swap is routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and passes that as the `sender` argument to `beforeSwap`. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. This makes it structurally impossible to enforce per-user swap restrictions for router-mediated swaps.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly: [4](#0-3) 

So `msg.sender` at the pool level is the **router**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` — a single binary flag for the entire router contract — rather than `allowedSwapper[pool][user]`.

### Impact Explanation

This creates two mutually exclusive failure modes for any pool using `SwapAllowlistExtension`:

1. **Router not allowlisted**: Individually allowlisted users cannot swap through the router at all, breaking the standard swap path for legitimate users.
2. **Router allowlisted**: Every user on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees only the router's address and passes the check. The curated pool is effectively open to all.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The allowlist guard is structurally bypassed for the router path.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol. Any pool that configures `SwapAllowlistExtension` and also needs to support router-based swaps (the normal user flow) will be forced to allowlist the router, immediately opening the pool to all users. The trigger requires no special privileges — any user can call `exactInputSingle` on the router.

### Recommendation

The pool should pass the original end-user identity through the swap path. One approach: store the initiating user in transient storage at the router entry point and expose it via a callback or a dedicated interface that the extension can query. Alternatively, the pool could accept an explicit `swapper` parameter distinct from `msg.sender`, validated via a callback, so the extension sees the real user rather than the intermediary.

A simpler mitigation: document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension level by reverting when `msg.sender` (the pool's caller, i.e., the router) is not itself the allowlisted address.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` intending to allow only Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was explicitly excluded from.

The `SwapAllowlistExtension` check at line 37 passes because it sees the router (allowlisted) rather than Bob (not allowlisted): [5](#0-4)

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
