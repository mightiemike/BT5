### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted rather than the **actual user**. Any non-allowlisted user can bypass a curated pool's swap allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
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

Because the router is `msg.sender` of `pool.swap()`, the extension receives `sender = address(router)`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

Two broken outcomes follow:

1. **Allowlist bypass**: If the pool admin allowlists the router (so that any legitimate user can reach the pool through the router), every non-allowlisted user can also bypass the gate by routing through the same public router.
2. **Allowlisted users locked out of the router**: If the pool admin does not allowlist the router, every individually allowlisted user is silently blocked when they try to use the router, even though they are permitted by the allowlist.

The same structural problem applies to multi-hop `exactInput` and the recursive `exactOutput` path, where intermediate hops also call `pool.swap()` with the router as `msg.sender`.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The router is a public, permissionless contract. A non-allowlisted user can execute swaps at the pool's oracle-derived prices, draining LP value or trading against restricted liquidity that the pool admin intended to protect. This is a direct loss of the curation policy and constitutes a broken core pool functionality with fund-impacting consequences for LPs on allowlisted pools.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed for the protocol. Any user who discovers the mismatch can immediately exploit it by calling `exactInputSingle` or `exactInput` on an allowlisted pool. No privileged access, special tokens, or unusual setup is required. The router is already deployed and publicly callable.

### Recommendation

The `sender` forwarded to extensions must represent the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

1. **In the router**: Store the original `msg.sender` in transient storage alongside the callback context (already done for the payer) and expose it as a `swapInitiator` that the pool can read. Alternatively, accept an explicit `swapper` parameter and forward it to the pool.
2. **In the pool / extension interface**: Add a dedicated `swapInitiator` field to the `beforeSwap` / `afterSwap` hook arguments, distinct from `sender` (the direct pool caller), so allowlist extensions can gate the true originator.

Until the interface is extended, the `SwapAllowlistExtension` should document that it only gates direct pool callers and is incompatible with router-mediated flows.

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E.
  - Pool admin calls E.setAllowedToSwap(P, router, true)
    (necessary so that allowlisted users can reach P through the router).
  - Alice (non-allowlisted) is NOT in allowedSwapper[P].

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
  2. Router calls P.swap(recipient, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. ExtensionCalling encodes sender = router and calls E.beforeSwap(router, ...).
  5. E checks allowedSwapper[P][router] == true → passes.
  6. Swap executes at oracle price; Alice receives output tokens.
  7. Alice, who was never allowlisted, has successfully traded on the curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
