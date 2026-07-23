Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Originating User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of the `pool.swap()` call — the router contract — not the originating user. When a pool admin allowlists the router to enable router-mediated swaps, every public user can bypass the per-user swap allowlist by routing through `MetricOmmSimpleRouter`. This breaks the core curation invariant of allowlisted pools.

## Finding Description
In `SwapAllowlistExtension.beforeSwap` (L37), the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the value forwarded from the pool. In `MetricOmmPool.swap` (L230–231), the pool calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`. [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly at L72–80: [2](#0-1) 

The pool receives `msg.sender = router`. It passes `sender = router` to `_beforeSwap`, which forwards it to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. [3](#0-2) 

The pool admin faces an inescapable dilemma: not allowlisting the router makes the router unusable for this pool; allowlisting the router opens the bypass to all public users. The router has no caller-level access control.

## Impact Explanation
A pool admin who deploys a curated pool (KYC-gated, institutional-only, or regulatory-restricted) and configures `SwapAllowlistExtension` to gate specific swappers cannot prevent non-allowlisted users from trading if the router is allowlisted. The swap executes at the live oracle price, the pool's bin state changes, and the non-allowlisted user receives output tokens. This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a pool admin-configured access control gate, violating the invariant that "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it." [4](#0-3) 

## Likelihood Explanation
The router is the primary UX path for end users. Any pool that wants to support router-mediated swaps must allowlist the router, which immediately opens the bypass to all users. The attacker needs no special privilege, no unusual token, and no complex setup — a single call to `exactInputSingle` suffices. The condition is met by default for any allowlisted pool that also supports router usage.

## Recommendation
The extension must gate on the originating user, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Maintain a trusted router registry**: When `sender` is a known router, fall back to checking a user identity embedded in `extensionData`; otherwise check `sender` directly.
3. **Document incompatibility**: Enforce at the extension level that `SwapAllowlistExtension` is incompatible with router-mediated swaps by reverting when `sender` is a known router address.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)  // to enable router swaps
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) with msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true → passes
8. Swap executes; attacker receives output tokens despite not being allowlisted.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
