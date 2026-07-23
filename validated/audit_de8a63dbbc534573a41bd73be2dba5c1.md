Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of originating EOA, enabling allowlist bypass for all router-mediated swaps — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. A pool admin who allowlists the router to enable normal router usage for any allowlisted user inadvertently grants every on-chain user access to the curated pool, completely nullifying the allowlist for router-mediated paths.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The effective check is `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool never sees the originating EOA — only the router address. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

The `IMetricOmmExtensions.beforeSwap` interface carries only `sender` and `recipient` — no `owner`/`originator` field — so there is no in-band way for the extension to recover the originating EOA without out-of-band encoding.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the economic beneficiary), not `sender` (the immediate caller), because `addLiquidity` carries a distinct `owner` argument through the call chain. The swap path has no equivalent.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension` on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` — the natural step to let any allowlisted user use the standard periphery.
3. Attacker (not on allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — `msg.sender` = router.
5. Pool calls `_beforeSwap(router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker trades on a curated pool they were explicitly excluded from.

No existing guard intercepts this: `BaseMetricExtension` only validates that the caller is a known pool (`msg.sender`), not the identity of the originating user.

## Impact Explanation
Two fund-impacting outcomes:

1. **Allowlist bypass (High):** Once the router is allowlisted (required for any allowlisted user to use the standard periphery), every unprivileged user can trade on the curated pool. The `SwapAllowlistExtension` provides zero protection for router-mediated swaps. This is a direct admin-boundary break: the pool admin's access-control policy is silently nullified by a design flaw in the extension.

2. **Broken core swap functionality (Medium):** If the admin does *not* allowlist the router, allowlisted EOAs cannot use `MetricOmmSimpleRouter` at all. Their only path is a direct `pool.swap()` call, which requires implementing `IMetricOmmSwapCallback`. The primary supported periphery is unusable for the very users the pool was designed to serve.

Both outcomes meet the allowed impact gate: broken core pool functionality and admin-boundary break.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router — a natural and expected operational step for any curated pool intending to support the standard periphery. No attacker privilege is required beyond calling the public router. The condition is likely to be met in any real deployment of `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

## Recommendation
Pass the originating user identity through the swap path so the extension can gate on the correct actor:

1. **Preferred — encode originator in `extensionData`:** The router encodes `msg.sender` (the originating EOA) into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` decodes and checks this value. No core changes required; the extension must validate the encoding source is trustworthy (e.g., by checking `msg.sender` is a known router).

2. **Alternative — add `originator` to `beforeSwap` interface:** Extend `IMetricOmmExtensions.beforeSwap` with an explicit `originator` parameter that the pool populates from a trusted source (e.g., a transient-storage slot set by the router before calling `swap`). This mirrors how `beforeAddLiquidity` uses `owner` rather than `sender`.

Either way, the extension must check the actor who bears the economic consequence of the swap, not the intermediate contract relaying the call.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary for any allowlisted user to use the router)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not on allowlist) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker trades on a curated pool they were explicitly excluded from
  - SwapAllowlistExtension provides zero protection for router-mediated swaps
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only the router, assert that an address not in `allowedSwapper` can successfully call `MetricOmmSimpleRouter.exactInputSingle` and receive output tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
