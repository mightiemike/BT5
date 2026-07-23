### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is always `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end-user. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks `allowedSwapper[pool][sender]`.**

The extension uses `msg.sender` (the pool) as the mapping key and the forwarded `sender` as the identity to gate: [3](#0-2) 

**Step 3 — MetricOmmSimpleRouter calls `pool.swap()` directly, making itself `msg.sender`.**

Every router entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly. The pool therefore sees `msg.sender = router`: [4](#0-3) 

The same holds for multi-hop and recursive exact-output paths: [5](#0-4) 

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.**

The pool admin intends to allowlist specific end-users. But the check that actually executes is against the router address. Two outcomes follow:

| Pool admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is blocked from using the router; they must call the pool directly. |
| Router **allowlisted** (to enable router-mediated swaps) | **Every user**, including those not on the allowlist, can bypass the gate by routing through the router. |

The second scenario is the critical one. A pool admin who wants to allow normal UX (router-mediated swaps) for their allowlisted users must allowlist the router, which simultaneously opens the pool to all users.

### Impact Explanation

The swap allowlist is the primary access-control mechanism for restricted pools. Once the router is allowlisted, any unprivileged address can execute swaps on a pool that was intended to be gated. This allows unauthorized parties to drain LP value through arbitrage or directional trading on pools that were designed to be private or permissioned. The impact is a direct loss of LP principal and protocol fees above Sherlock thresholds.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap interface. Any pool that wants to support normal user interaction must allowlist the router. The bypass is therefore reachable by any unprivileged user on any allowlisted pool that also uses `SwapAllowlistExtension`. No special privileges, malicious setup, or non-standard tokens are required.

### Recommendation

The extension must gate the actual end-user, not the intermediary. Two approaches:

1. **Forward the original caller via `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and checks that address instead of the forwarded `sender`. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`.

Either way, the extension must be updated so that `allowedSwapper[pool][realUser]` is the check that executes, regardless of whether the user calls the pool directly or through the router.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router UX
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended gated user
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes successfully for bob despite bob not being on the allowlist.

Result: bob swaps on a restricted pool, bypassing the allowlist entirely.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
