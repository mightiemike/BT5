Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any unprivileged user to bypass per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to function), every unprivileged user can bypass the per-user allowlist by routing through the public router, rendering the access control guard structurally inoperable for the router path.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender` to the extension hook:**

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is always `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
  extensionData
);
``` [1](#0-0) 

**Router calls `pool.swap()` directly; the actual user's address is never forwarded:**

`MetricOmmSimpleRouter.exactInputSingle` stores the user's address only in transient callback context for payment settlement, then calls `pool.swap()` with the router as `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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
``` [2](#0-1) 

The pool's `swap()` signature has no `originator` parameter; there is no mechanism to pass the actual user through to the extension hook.

**The allowlist extension checks the router address, not the actual user:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is the address the pool passed — which is the router when the user goes through `MetricOmmSimpleRouter`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The structural dilemma this creates for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do NOT allowlist the router | No user can swap through the router, even individually allowlisted ones |
| Allowlist the router | Every user — including explicitly disallowed ones — can bypass the allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user restrictions. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any unprivileged user calling any of the router's swap functions. The disallowed user receives pool output tokens at oracle-anchored prices, draining LP value that was intended to be accessible only to allowlisted counterparties. The allowlist guard — the sole access-control mechanism on the swap path — is rendered ineffective for the public router entrypoint. This constitutes a broken core pool functionality causing direct loss of LP assets and an admin-boundary break where an unprivileged path bypasses a configured access control. [5](#0-4) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint for the protocol. Any user who discovers that a pool uses `SwapAllowlistExtension` can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The bypass is reachable on every block as long as the router is allowlisted, and the router must be allowlisted for any router-mediated swap to function at all. [6](#0-5) 

## Recommendation

The pool's `swap()` function should accept an explicit `originator` parameter (the actual user) that is forwarded to extension hooks as `sender`, separate from `msg.sender` (the caller/router). Alternatively, `SwapAllowlistExtension` should be redesigned to gate on an originator passed through `extensionData` (with the router responsible for injecting the real user's address), or the router should be prohibited from being allowlisted while per-user restrictions are active. The most robust fix is to add an `originator` field to the pool's `swap()` signature and forward it through `_beforeSwap` so extensions always see the economically responsible actor. [7](#0-6) 

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required for router-mediated swaps to work.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives pool output tokens despite being explicitly excluded from the allowlist.

The guard configured to protect LP funds from disallowed counterparties is silently bypassed on every router-mediated swap. [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
