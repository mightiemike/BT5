### Title
`SwapAllowlistExtension` Gates by Router Address Instead of User Identity, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the user. This means the allowlist gates the router address, not the actual swapper. A pool admin who allowlists the router to enable normal user access inadvertently opens the gate to every user, defeating the allowlist entirely. Conversely, a pool admin who allowlists specific EOA/user addresses finds those users permanently blocked from using the standard router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router, so `sender` passed to the extension is the router address. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This is structurally opposite to `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates by `owner` (the actual beneficiary), allowing `MetricOmmPoolLiquidityAdder` to work transparently while still enforcing per-user deposit restrictions.

**Bypass path:**
A pool admin who wants to allow normal users to swap through the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any user** — including those the admin intended to block — can call `router.exactInputSingle(...)` and pass the allowlist check, because the check only sees the router address.

**Blocking path:**
A pool admin who allowlists specific EOA addresses (e.g., `setAllowedToSwap(pool, alice, true)`) finds that Alice cannot swap through the router at all, because the extension sees `sender = router`, not `sender = alice`. Alice would need to deploy her own contract implementing `IMetricOmmSwapCallback` and call the pool directly.

### Impact Explanation

- **Guard bypass (Critical/High):** If the pool admin allowlists the router to enable standard user access, the `SwapAllowlistExtension` guard is completely bypassed for all users. Any address can execute swaps on a pool that was intended to be restricted, draining liquidity at oracle-anchored prices without authorization.
- **Broken core swap functionality (Medium):** If the pool admin allowlists specific user addresses, those users cannot use the standard `MetricOmmSimpleRouter` periphery. The swap path is unusable for the intended participants.

### Likelihood Explanation

The `DepositAllowlistExtension` gates by `owner` (user identity), creating a natural expectation that `SwapAllowlistExtension` behaves symmetrically. A pool admin configuring both extensions will almost certainly allowlist user addresses in the swap extension, triggering the blocking path. Alternatively, a pool admin who allowlists the router to "enable normal swaps" triggers the bypass path. Both are reachable through normal, non-malicious configuration.

### Recommendation

Change `SwapAllowlistExtension.beforeSwap` to accept and check a dedicated `recipient` or introduce a separate `swapper` identity field, or — more practically — mirror the `DepositAllowlistExtension` pattern by checking the `recipient` argument (second parameter) rather than `sender`. Alternatively, document explicitly that the allowlist gates the **calling contract** (not the EOA), and require pool admins to allowlist specific router contracts rather than user addresses. If per-user gating is desired, the pool must pass the original user identity through `extensionData` and the extension must decode it.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps.
3. A non-allowlisted user `bob` calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Bob, who was never individually allowlisted, successfully swaps on a restricted pool.

Alternatively:
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allow only Alice.
2. Alice calls `router.exactInputSingle({pool: pool, ...})`.
3. Extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert.
4. Alice is blocked from the standard periphery despite being explicitly allowlisted.

---

**Relevant code locations:**

`SwapAllowlistExtension.beforeSwap` — checks `sender` (= router when called via router): [1](#0-0) 

`MetricOmmPool.swap` — passes `msg.sender` (= router) as `sender` to extension: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` — router is `msg.sender` to the pool: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` — correctly gates by `owner` (user identity), not `sender`: [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
