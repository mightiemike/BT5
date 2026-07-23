### Title
`SwapAllowlistExtension` Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. Any unprivileged user can therefore bypass the swap allowlist on a curated pool by routing through the public router, completely defeating the intended access control.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument passed by the pool. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to the extension dispatcher:

```solidity
_beforeSwap(msg.sender, recipient, ...);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [3](#0-2) 

When a user calls the pool directly, `sender = user`. When a user calls through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` and the pool's `msg.sender` is the router: [4](#0-3) 

So `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This creates an impossible situation for the pool admin:

- **Router NOT allowlisted**: router-mediated swaps fail for everyone, including allowlisted users, breaking the supported periphery path.
- **Router IS allowlisted**: any user can bypass the per-user allowlist by routing through the public router.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of which call `pool.swap(...)` with `msg.sender = router`. [5](#0-4) 

### Impact Explanation
A disallowed user can trade on a curated pool (e.g., KYC-gated, institution-only) by routing through `MetricOmmSimpleRouter`. The swap allowlist protection is completely defeated for router-mediated swaps. LPs on curated pools suffer direct loss from trades by disallowed users — the pool admin's intended access boundary is silently bypassed by any unprivileged caller.

### Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` with any pool address and any `extensionData`. No special privileges, no malicious setup, and no admin cooperation are required. The bypass is reachable in a single transaction.

### Recommendation
The extension must check the actual end user, not the direct caller of the pool. Concrete options:

1. Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it (requires a trusted encoding convention).
2. Have the pool store the original caller in transient storage at entry and expose it to extensions via a dedicated accessor.
3. Document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter`, and reject pool configurations that combine both.

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true) to support
   router-mediated swaps for allowlisted users.
3. Non-allowlisted attacker calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       tokenIn: token0,
       tokenOut: token1,
       zeroForOne: true,
       amountIn: X,
       ...
     })
4. Router calls pool.swap(recipient, ...) — pool's msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
7. Swap executes. Attacker trades on the curated pool without being
   individually allowlisted, bypassing the intended access control.
```

The root cause is the identity mismatch: `sender` passed to the extension is the direct caller of the pool (`msg.sender` at the pool boundary), not the economic actor who initiated the trade. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
