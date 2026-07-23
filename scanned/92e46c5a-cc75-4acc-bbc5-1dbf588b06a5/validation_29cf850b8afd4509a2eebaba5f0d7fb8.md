### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address rather than the actual end-user's address. If the pool admin allowlists the router (the only way to permit router-mediated swaps on a curated pool), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap()`, `sender` is the router's address, not the end-user's address.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps always revert — router is unusable on this pool |
| **Allowlist the router** | `allowedSwapper[pool][router] = true` → **every user** who calls through the router passes the check, regardless of individual allowlist status |

There is no configuration that simultaneously permits router-mediated swaps and restricts which end-users may swap. The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position beneficiary), which the liquidity adder passes correctly as the actual user.

### Impact Explanation

Any user excluded from the per-user allowlist on a curated pool can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting that pool, provided the router is allowlisted. This completely defeats the purpose of the `SwapAllowlistExtension` on any pool that also supports router-mediated swaps. Unauthorized swaps on a curated pool can drain LP principal through bad-price execution or violate the pool's intended access policy, constituting a direct loss of LP assets above contest thresholds.

### Likelihood Explanation

The likelihood is high. Any production pool that deploys `SwapAllowlistExtension` to restrict swappers while also wanting to support the standard periphery router must allowlist the router — the only supported periphery swap path. The moment the router is allowlisted, the allowlist is effectively open to all users. The attacker needs no special privileges: a single call to the public router suffices.

### Recommendation

The extension must check the actual end-user's identity, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Trusted forwarder pattern**: Have the router encode the original `msg.sender` in `extensionData` and have the extension verify it when `sender` is a known trusted router address.
2. **Check `recipient` instead of `sender`**: For swap allowlisting, gate on the output recipient rather than the input payer; this is already the correct identity for many curated-pool use cases and is unaffected by router intermediation.

Additionally, document clearly that `sender` on the swap hook is the immediate pool caller, not the end-user, so extension authors are not misled.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin allowlists `alice` (trusted LP) but not `bob` (untrusted user).
3. Pool admin also allowlists the router address so `alice` can use the router: `setAllowedToSwap(pool, router, true)`.
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool passes `msg.sender = router` as `sender` to `_beforeSwap`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → check passes.
7. `bob` successfully swaps on the curated pool despite never being individually allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
