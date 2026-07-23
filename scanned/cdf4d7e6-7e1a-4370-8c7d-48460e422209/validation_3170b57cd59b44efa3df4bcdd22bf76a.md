### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If a pool admin allowlists the router to support normal periphery usage, every unprivileged user can bypass the individual-user allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router does not forward the original caller's identity to the pool. Therefore, the extension sees `sender = router`, not the end user. For any router-mediated swap to succeed on an allowlisted pool, the admin must add the router address to `allowedSwapper[pool][router]`. Once that entry exists, every user — including those the admin explicitly excluded — can call `exactInputSingle` or `exactInput` through the router and the extension will pass them, because the check resolves to `allowedSwapper[pool][router] == true`.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties). To support normal UX through the periphery router, the admin must allowlist the router. The moment the router is allowlisted, the allowlist provides zero protection: any address can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the pool. The allowlist guard silently fails open for all router-mediated swaps, which is the primary supported swap path for end users. This constitutes a direct bypass of a core pool access-control mechanism, enabling unauthorized trading on pools that were designed to be curated.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented swap entrypoint for end users. Pool admins who configure a `SwapAllowlistExtension` and also want their users to use the router will inevitably allowlist the router address. The bypass requires no special privileges, no flash loans, and no multi-step setup — any address can call `exactInputSingle` on the router pointing at the curated pool. The combination of a natural admin action (allowlisting the router) and a publicly reachable entrypoint makes this highly likely to be triggered in practice.

### Recommendation

The `SwapAllowlistExtension` should not rely on the `sender` argument forwarded by the pool when the swap may originate from a pass-through router. Two complementary fixes:

1. **Extension-side**: Accept an optional `bytes calldata extensionData` field that carries the verified end-user address (signed or encoded by the router), and check that address instead of `sender` when present.
2. **Router-side**: The router should encode the original `msg.sender` into the `extensionData` it forwards to the pool, allowing allowlist extensions to recover and check the true initiator.

Alternatively, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that pool admins must never allowlist the router address if they intend to enforce per-user restrictions.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to support router UX
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. bob's swap executes successfully despite not being on the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist completely bypassed
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
