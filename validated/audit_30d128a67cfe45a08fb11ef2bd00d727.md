### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address to enable router-based swaps inadvertently opens the curated pool to every user, because the per-user allowlist is never consulted.

### Finding Description

**Actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist:

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
``` [1](#0-0) 

**What `sender` actually is**

`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension: [3](#0-2) 

**How `MetricOmmSimpleRouter` calls the pool**

`exactInputSingle` calls `pool.swap()` directly from the router contract, so `msg.sender` inside the pool is the router, not the end user:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The broken invariant**

The pool admin configures the allowlist expecting it to gate individual users. But the extension only ever sees the router address as `sender`. The admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Allowlist individual users | Those users cannot swap through the router (router address fails the check) |
| Allowlist the router address | Every user on-chain can bypass the per-user gate |

There is no configuration that simultaneously enables router-based swaps and enforces per-user curation.

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, regulatory-restricted, or market-maker-only) and allowlists the router to support the standard periphery path inadvertently opens the pool to all users. Any address can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the curated pool and the `SwapAllowlistExtension` will pass because `allowedSwapper[pool][router] == true`. The curation boundary is completely erased for every swap that enters through the router, which is the primary supported public entrypoint.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap interface documented and shipped alongside the extension. A pool admin who wants users to be able to swap through the router must allowlist the router. The misconfiguration is the natural, expected setup for any pool that intends to support the periphery. No malicious intent is required; the admin simply follows the obvious integration path.

### Recommendation

The `SwapAllowlistExtension` must check the actual economic actor, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the end-user address in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the end user, though this breaks for multi-hop paths where intermediate recipients are the router itself.
3. **Separate router-level allowlist**: Deploy a wrapper that enforces per-user checks before calling the pool, and allowlist only that wrapper in the extension.

### Proof of Concept

```
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true)
   — intending only Alice to swap.
3. Pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
   — intending to allow Alice to use the router.
4. Eve (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
5. Router calls pool.swap(...) → msg.sender inside pool = router.
6. _beforeSwap(sender=router, ...) → SwapAllowlistExtension checks
       allowedSwapper[pool][router] == true → passes.
7. Eve's swap executes successfully despite not being individually allowlisted.
```

The allowlist invariant — "only Alice may swap" — is violated by any user who routes through `MetricOmmSimpleRouter`, because the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][eve]`. [5](#0-4) [2](#0-1) [4](#0-3)

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
