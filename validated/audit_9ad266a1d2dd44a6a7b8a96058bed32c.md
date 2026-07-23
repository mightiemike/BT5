Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on router address instead of end-user, enabling allowlist bypass or blocking authorized router swaps - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the end-user. This causes the allowlist to evaluate the router's authorization rather than the actual trader's, producing two mutually exclusive broken states: universal bypass if the router is allowlisted, or universal blocking of router-based swaps for individually allowlisted users.

## Finding Description

In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for the mapping key). `sender` is the first parameter forwarded from `ExtensionCalling._beforeSwap`, which encodes `msg.sender` of the pool's own `swap()` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router when routing through periphery
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly:

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

Inside the pool, `msg.sender` is the router address. The extension therefore receives `sender = router` and evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The same misbinding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the second parameter), which is an explicit, stable identity passed through the call chain independently of who the intermediary sender is. The swap path has no equivalent stable identity — it only has `sender`, which changes based on whether the user calls the pool directly or through the router.

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified users) faces two mutually exclusive broken states:

1. **Allowlist bypass (High):** If the admin allowlists the router address to enable router-based swaps, every user — including those not individually allowlisted — can bypass the per-user restriction by routing through `MetricOmmSimpleRouter`. Unauthorized parties receive token output from the curated pool, directly violating the curation policy with fund-impacting consequences.

2. **Router path blocked (Medium):** If the admin allowlists only individual users and not the router, those users cannot swap through `MetricOmmSimpleRouter` at all despite being individually authorized. They must implement `IMetricOmmSwapCallback` themselves to call `pool.swap()` directly, which is not the standard supported flow.

Case (1) constitutes broken core pool functionality and a direct policy bypass with fund-impacting consequences — unauthorized parties trade in a pool configured to exclude them.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. Any user of a curated pool who uses the router — the expected and documented path — triggers this misbinding. The pool admin has no way to configure `SwapAllowlistExtension` to correctly enforce per-user restrictions while also allowing router-based swaps, because the extension has no access to the true originating user address. The bypass is reachable by any unprivileged trader with no special preconditions beyond the pool having `SwapAllowlistExtension` configured.

## Recommendation

`SwapAllowlistExtension` must identify the true end-user, not the intermediary router. Two approaches:

1. **`extensionData` forwarding:** Have `MetricOmmSimpleRouter` encode the actual `msg.sender` (the end-user) into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of (or in addition to) `sender`. This requires a convention between the router and the extension.

2. **Dual-check:** Check both `sender` and a user address decoded from `extensionData`, falling back to `sender` when `extensionData` is empty (for direct pool callers).

3. **Documentation gate (minimum):** Document clearly that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that curated pools using it must not allowlist the router, and that allowlisted users must call `pool.swap()` directly.

## Proof of Concept

**Scenario: Allowlist bypass via router**

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (intending to allow router-based swaps for allowlisted users)

Attack:
  - Mallory (not individually allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: mallory, ...})
  - Router calls: pool.swap(mallory, ...)  [msg.sender = router inside pool]
  - Pool calls: extension.beforeSwap(router, mallory, ...)
  - Extension checks: allowedSwapper[pool][router] == true  ✓
  - Swap executes — Mallory receives tokens from the curated pool
    despite never being individually authorized.

Result:
  - The per-user allowlist is completely bypassed for all router users.
```

**Scenario: Authorized user blocked via router**

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls: swapExtension.setAllowedToSwap(pool, alice, true)

Alice attempts to swap via router:
  - router.exactInputSingle({pool: pool, recipient: alice, ...})
  - Router calls: pool.swap(alice, ...)  [msg.sender = router inside pool]
  - Pool calls: extension.beforeSwap(router, alice, ...)
  - Extension checks: allowedSwapper[pool][router] == false  ✗
  - Revert: NotAllowedToSwap()

Result:
  - Alice cannot use the standard periphery path despite being allowlisted.
```

**Foundry test plan:**
1. Deploy pool with `SwapAllowlistExtension`.
2. `setAllowedToSwap(pool, router, true)`.
3. Call `router.exactInputSingle(...)` from an address not individually allowlisted.
4. Assert swap succeeds — demonstrating the bypass.
5. Reset: `setAllowedToSwap(pool, router, false)`, `setAllowedToSwap(pool, alice, true)`.
6. Call `router.exactInputSingle(...)` from `alice`.
7. Assert revert with `NotAllowedToSwap()` — demonstrating the blocking. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
