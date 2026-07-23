### Title
`SwapAllowlistExtension` gates on direct pool caller (`sender`) instead of the originating user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-user allowlist by checking `sender`, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the originating user. If the pool admin allowlists the router (the only way to permit router-mediated swaps on a curated pool), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the public periphery contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is whatever the pool passed as the first argument to `_beforeSwap`. In `MetricOmmPool.swap()`, that argument is `msg.sender` of the pool call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router, so `sender` passed to `beforeSwap` is the router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin configuration | Effect |
|---|---|
| Router NOT allowlisted | All router-mediated swaps revert, even for allowlisted users |
| Router IS allowlisted | Every user can bypass the per-user allowlist via the router |

There is no configuration that allows specific users to swap via the router while blocking others.

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist correctly checks `owner` (the position owner, passed explicitly by the caller), not `sender` (the direct pool caller). `MetricOmmPoolLiquidityAdder` passes the actual user as `owner`, so the deposit allowlist gates the correct economic actor. The swap allowlist has no equivalent mechanism.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, protocol-controlled addresses, or whitelisted market makers). Any non-allowlisted user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the curated pool. The router is a public, permissionless contract. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. The curated pool's LP assets are exposed to unrestricted swap flow, directly violating the pool admin's intended access policy and potentially causing direct loss of LP principal through toxic flow the allowlist was designed to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery swap entrypoint. Any user aware of the allowlist restriction has an obvious incentive to route through the router. The pool admin must allowlist the router to support normal user flows, making the bypass trivially reachable. No special knowledge beyond the public contract addresses is required.

---

### Recommendation

Gate the allowlist on the originating user rather than the direct pool caller. Two approaches:

1. **Check `recipient` instead of `sender`:** The `recipient` is the address that receives swap output and is the economically meaningful actor. However, `recipient` can also be set to a third party, so this is imperfect.

2. **Mirror `DepositAllowlistExtension`'s pattern:** Add an explicit `swapper` parameter to the pool's swap interface (analogous to `owner` in `addLiquidity`) that the pool admin controls and the extension gates on. The router would forward `msg.sender` as this parameter.

3. **Short-term mitigation:** Document that `SwapAllowlistExtension` only gates direct `pool.swap()` calls and cannot enforce per-user policy for router-mediated swaps. Pool admins must not allowlist the router if per-user gating is required.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is allowlisted
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         ...
     })
  2. Router calls pool.swap(bob, ...) with msg.sender = router
  3. pool._beforeSwap(router, bob, ...) is called
  4. SwapAllowlistExtension.beforeSwap(router, bob, ...) checks:
       allowedSwapper[pool][router] == true  → passes
  5. bob's swap executes successfully on the curated pool
  6. Allowlist policy is completely bypassed
```

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
