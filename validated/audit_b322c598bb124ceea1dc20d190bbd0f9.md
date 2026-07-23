Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension` is documented as gating `swap` by swapper address per pool, but its `beforeSwap` hook checks `allowedSwapper[msg.sender][sender]` where `sender` is `msg.sender` of `pool.swap()` — the router, not the end user. Any pool admin who allowlists the router (required for legitimate users to swap via the router) simultaneously opens the pool to every unprivileged address that routes through the same router, completely nullifying the per-user access control.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller; the router when routed
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this unchanged as the first positional argument to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = router
    )
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check passes whenever `allowedSwapper[pool][router]` is `true`. The actual user (`recipient`) is never inspected. [4](#0-3) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with the router as `msg.sender`, confirming the router is the `sender` forwarded to the extension: [5](#0-4) 

Additionally, the `beforeSwap` override drops the `onlyPool` modifier that `BaseMetricExtension.beforeSwap` carries, meaning the extension can be called directly by any address — not only registered pools: [6](#0-5) 

## Impact Explanation
This is an admin-boundary break: the pool admin's primary access-control mechanism — the per-user swap allowlist — is silently bypassed for every user who routes through `MetricOmmSimpleRouter`. Curated pools designed for KYC'd counterparties, institutional-only flows, or pools with favorable fee terms for specific market makers are fully open to unauthorized traders. The exact corrupted value is the extension's gate decision: `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][actualUser]`, causing the guard to return `true` for every unprivileged user who routes through the router. [3](#0-2) 

## Likelihood Explanation
- `MetricOmmSimpleRouter` is a standard, publicly deployed periphery contract accessible to any address.
- Any pool admin who wants allowlisted users to use the router **must** add the router to the allowlist; there is no alternative mechanism.
- Once the router is allowlisted (the expected production configuration), the bypass requires zero special privileges: any address calls the router, which calls the pool, and the extension passes.
- The attacker can verify the configuration entirely on-chain before acting. [7](#0-6) 

## Recommendation
Gate on the economically relevant actor. Two options:

1. **Check `recipient` instead of `sender`**: Change the check to `allowedSwapper[msg.sender][recipient]`. The output-token receiver is the user who benefits from the swap; when the router sets `recipient = user`, this closes the bypass.

2. **Require the router to attest the real user via `extensionData`**: Decode the actual user address from `extensionData` and check that address. More robust but requires router cooperation.

Additionally, restore the `onlyPool` modifier on `SwapAllowlistExtension.beforeSwap` (present in `BaseMetricExtension` but dropped by the override) to prevent direct calls from arbitrary addresses. [4](#0-3) 

## Proof of Concept
```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, user1, true)
   → allowedSwapper[pool][user1] = true
3. Pool admin calls setAllowedToSwap(pool, router, true)
   → allowedSwapper[pool][router] = true
   (required so user1 can swap via the router)

Attack
──────
4. attacker (not in allowlist) calls:
       router.exactInputSingle(pool, ..., recipient=attacker)
5. Router calls:
       pool.swap(recipient=attacker, ...)   // msg.sender = router
6. Pool calls _beforeSwap(sender=router, recipient=attacker, ...)
7. Extension evaluates:
       allowedSwapper[pool][router]  →  true  ✓  (passes)
8. Swap executes; attacker receives output tokens.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [3](#0-2) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
