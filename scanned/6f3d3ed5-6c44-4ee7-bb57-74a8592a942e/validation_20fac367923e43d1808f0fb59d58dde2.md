### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted — not the actual user. If the router is allowlisted (which is required for any router-mediated swap to work), every user on the network can bypass the per-pool swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the pool's `msg.sender` the **router**, not the end user: [4](#0-3) 

The router stores the real payer in transient storage for its own callback settlement, but never surfaces that identity to the pool or to any extension: [5](#0-4) 

This creates an irreconcilable conflict for any pool that uses `SwapAllowlistExtension`:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot swap through the router at all — broken core functionality |
| **Yes** | Every user on the network can bypass the allowlist by routing through the router |

The only escape is to require all allowlisted users to call `pool.swap` directly, which defeats the purpose of the periphery router and is not documented as a constraint anywhere.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. Because the extension sees the router address instead of the real trader, any non-allowlisted address can trade freely in the curated pool by using the standard router interface. This is a complete policy bypass: unauthorized users can drain the pool's liquidity at oracle-quoted prices, extract value from LP positions, and interact with the pool in ways the admin explicitly intended to prevent.

---

### Likelihood Explanation

The router is the canonical, documented swap interface for end users. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. The moment they do, the bypass is open to everyone. There is no in-protocol mechanism to distinguish real users behind the router, so the bypass is reachable by any address with no special privileges.

---

### Recommendation

The pool should forward the original caller's identity to extensions through a dedicated field rather than reusing `msg.sender`. One concrete approach: add an `originator` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates with `msg.sender` on direct calls and that the router populates (via `extensionData` or a separate field) with the real user address. `SwapAllowlistExtension` should then gate on `originator`, not on `sender`. Alternatively, the router can embed the real user address in `extensionData` and the extension can decode and verify it, provided the pool enforces that the embedded address matches the callback payer.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for router-mediated swaps
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes successfully despite not being on the allowlist.
``` [6](#0-5) [4](#0-3) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L181-215)
```text
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    int128 amount0Delta,
    int128 amount1Delta,
    uint256 protocolFeeAmount,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      AFTER_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.afterSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          packedSlot0Final,
          bidPriceX64,
          askPriceX64,
          amount0Delta,
          amount1Delta,
          protocolFeeAmount,
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
