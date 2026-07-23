Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Allowing Universal Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which is the immediate caller of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][endUser]`. Allowlisting the router — a prerequisite for any router-mediated swap — grants every public user a complete bypass of the intended access control.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (enforced by the `onlyPool` modifier in `BaseMetricExtension`) and `sender` is the first argument forwarded from the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then ABI-encodes this value as the first positional argument dispatched to the extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` from the pool's perspective:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181) — all router entry points call `pool.swap()` as `msg.sender = router`. [5](#0-4) 

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set.
2. Admin allowlists specific users: `setAllowedToSwap(pool, user1, true)`.
3. Admin allowlists the router so allowlisted users can trade through it: `setAllowedToSwap(pool, router, true)`.
4. Any non-allowlisted attacker calls `router.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps, completely bypassing the intended access control.

The `onlyPool` modifier in `BaseMetricExtension` only verifies the caller is a registered pool; it provides no protection against this bypass. [6](#0-5) 

## Impact Explanation

The swap allowlist is the primary mechanism for permissioned pools (KYC'd counterparties, institutional LPs, protocol-internal use). Once the router is allowlisted — which is operationally required for any allowlisted user to trade through the standard router — the guard is completely ineffective for all public users. This constitutes broken core pool functionality and an admin-boundary break: an unprivileged path (the public router) bypasses a configured access control, rendering the extension's purpose void.

## Likelihood Explanation

Likelihood is high. Any pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to trade through the standard router must allowlist the router address. There is no alternative path: the router is a public contract with no per-user identity forwarding. The moment the router is allowlisted, the bypass is universally available to any caller of the router. The condition is not a misconfiguration — it is the only operational setup that makes the router work with the extension.

## Recommendation

The `sender` parameter forwarded to `beforeSwap` must represent the economic actor (end user), not the routing intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension`**: maintain a `trustedRouter` registry; when `sender` is a trusted router, decode the true initiator from `extensionData` and check that address instead.
2. **Preferred — in the router/pool**: have `MetricOmmSimpleRouter` forward the original `msg.sender` as part of `extensionData`, and have the extension decode it; or add a dedicated `initiator` field to the `beforeSwap` hook signature so the extension always sees the end user regardless of routing depth.

## Proof of Concept

```solidity
// Pool admin sets up a restricted pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// ... deploy pool with ext in BEFORE_SWAP_ORDER ...

// Admin allowlists only user1 and the router (required for router-mediated swaps)
ext.setAllowedToSwap(pool, user1, true);
ext.setAllowedToSwap(pool, address(router), true);

// Non-allowlisted attacker bypasses the guard via the router
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✓ swap succeeds — allowlist bypassed because sender == router, which is allowlisted
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
