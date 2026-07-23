### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any unprivileged swapper to bypass the per-pool swap allowlist through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router contract**, not the end user. This wrong-actor binding creates an irreconcilable dilemma: either allowlisted users cannot use the router at all, or the pool admin must allowlist the router — which then lets **any** unprivileged address bypass the per-user allowlist by routing through it.

---

### Finding Description

**Actor binding in the pool:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**The allowlist check:**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the direct caller of `pool.swap`) and checks it against the per-pool allowlist: [3](#0-2) 

**The router is the direct caller:**

Every `MetricOmmSimpleRouter` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the router `msg.sender` of that call: [4](#0-3) 

So when a user routes through the router, `sender` seen by `SwapAllowlistExtension` is the **router address**, not the user's address.

**The dilemma this creates:**

| Admin configuration | Effect |
|---|---|
| Allowlist only specific users (not the router) | Allowlisted users **cannot** use the router — their addresses are never seen by the extension |
| Allowlist the router | **Any** address can bypass the per-user allowlist by routing through the router |
| Allowlist specific users **and** the router | Same bypass as above — the router entry overrides per-user gating |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is rendered either non-functional for router users or completely open to any swapper. In the bypass case, an unprivileged address can execute swaps on a pool that was explicitly designed to exclude them, draining LP value through unfavorable oracle-priced trades that the allowlist was meant to prevent. This is a broken core pool functionality / curation failure with direct LP fund impact.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (the natural step to enable normal user access) immediately opens the bypass. The trigger is a single unprivileged `exactInputSingle` call from any address.

---

### Recommendation

The `SwapAllowlistExtension` should check the **end user** rather than the direct caller of `pool.swap`. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Check `sender` and fall back to a router-forwarded identity**: Add a registry of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData` and check that address instead.

3. **Enforce allowlist at the router level**: Add a separate router-side allowlist check before calling `pool.swap`, so the extension only needs to gate direct pool callers.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // to enable router-based swaps
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  → passes
  - Swap executes for attacker despite attacker not being allowlisted
```

The attacker bypasses the curated allowlist entirely by routing through the standard periphery contract.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
