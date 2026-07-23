Audit Report

## Title
`SwapAllowlistExtension` checks the immediate caller (router) instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` binds to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that immediate caller is the router contract, not the originating user. Any pool admin who allowlists the router address (a natural action to enable router-mediated access for curated users) inadvertently opens the pool to every unprivileged caller, completely defeating the allowlist invariant.

## Finding Description

**Pool binds `msg.sender` as `sender` in `_beforeSwap`:**

`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

When the call originates from `MetricOmmSimpleRouter`, `msg.sender` here is the router contract address.

**Extension checks that `sender` against the per-pool allowlist:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (enforced by `onlyPool`); `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

**Router never forwards the originating user to the pool:**

`MetricOmmSimpleRouter.exactInputSingle()` stores the original `msg.sender` only in transient callback context for payment, and calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The original user's address is never passed to `pool.swap()` and therefore never reaches `beforeSwap`. The same structural problem exists in `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181).

**The impossible admin choice:** The pool admin has no configuration that achieves "allow specific users to swap through the router":
- Do not allowlist the router → allowlisted users cannot use the router at all.
- Allowlist the router → every user, allowlisted or not, bypasses the restriction by routing through the router.

## Impact Explanation
A non-allowlisted user can execute swaps on a pool explicitly configured to restrict access to a curated set of addresses. The bypass is achieved through the canonical, supported periphery path (`MetricOmmSimpleRouter`). The allowlist check silently fails open for every router-mediated swap once the router is allowlisted, violating the core invariant that a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. Unauthorized swaps drain pool liquidity at oracle-derived prices, constituting a direct loss of LP-owned assets on any pool where the allowlist is the primary access-control mechanism.

## Likelihood Explanation
Pool admins who deploy a `SwapAllowlistExtension` pool and want their allowlisted users to access it through the standard router will naturally call `setAllowedToSwap(pool, router, true)`. The admin's intent ("allow my curated users to use the router") is reasonable and expected; the consequence ("all users can now bypass the allowlist") is non-obvious from the extension's interface. `MetricOmmSimpleRouter` is the primary user-facing swap entry point in the periphery layer, so this path is exercised by every normal user.

## Recommendation
The `sender` argument forwarded to extension hooks must represent the originating user, not the intermediate contract. Two viable fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (e.g., as a leading 20-byte prefix), and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.
2. **Pool-side**: Add an explicit `originator` field to the `swap()` call signature that the router populates with `msg.sender`, and pass it through `_beforeSwap` to extensions.

Option 2 is cleaner but requires a core interface change. Option 1 can be deployed without touching `metric-core` but requires the extension to trust the router's encoding.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension; allowlist only `alice`.
2. Pool admin calls:
       setAllowedToSwap(pool, router, true)
   (intending to let alice use the router).
3. Non-allowlisted `bob` calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(); pool passes msg.sender=router to _beforeSwap.
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. Bob's swap executes on the curated pool despite not being allowlisted.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, then call `exactInputSingle` from `bob` and assert it succeeds — confirming the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
