Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any address to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to gate swaps on curated pools to specific allowlisted addresses. However, `MetricOmmPool.swap` passes `msg.sender` — the immediate caller — as the `sender` argument to the extension hook. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to enable standard UX inadvertently grants unrestricted swap access to every address.

## Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`.**

In `MetricOmmPool.swap`, the first argument to `_beforeSwap` is hardcoded as `msg.sender`: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router's address, not the end user.

**Step 2 — The router calls `pool.swap` directly without forwarding the original caller.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with no mechanism to pass the original `msg.sender` (the end user) to the pool: [2](#0-1) 

The router's own address becomes `msg.sender` at the pool for all swap variants (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

**Step 3 — The extension checks the router address, not the end user.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The allowlist state is keyed by `(pool, swapper)` with no per-user granularity inside a router-level entry: [4](#0-3) 

Allowlisting the router is a blanket grant to all callers of the router. There is no existing guard that recovers the true originator from `extensionData` or any other channel.

## Impact Explanation

Any non-allowlisted address can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (necessary for any allowlisted user to use the standard UX), the allowlist is completely ineffective. Non-allowlisted users can swap on pools designed to restrict access to specific addresses (e.g., KYC-gated, institutional-only), consuming liquidity at oracle-anchored prices and causing direct losses to LPs whose pool was configured under the assumption that only vetted counterparties would trade. This constitutes broken core pool functionality and an admin-boundary break reachable by any unprivileged address.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and also wants to support the router is affected. The pool admin has no in-protocol mechanism to simultaneously allow the router and enforce per-user allowlist policy. The bypass is reachable by any unprivileged address with zero preconditions beyond the admin having allowlisted the router, which is a necessary operational step for standard UX.

## Recommendation

The extension must check the economically relevant actor — the address that initiated the trade — not the immediate caller of `pool.swap`. Concrete options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it against a trusted-router registry. Requires a trusted router assumption but is backward-compatible.
2. **Reject router calls unless the router is excluded from allowlist enforcement**: The extension reverts if `sender` is a known router address and the router is not explicitly excluded from allowlist enforcement.
3. **Redesign the hook signature**: Pass a separate `originator` field distinct from `sender` so the pool can forward the true end-user identity through the call chain.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin allowlists the router (necessary for allowlisted users to use standard UX):
       extension.setAllowedToSwap(pool, address(router), true)
3. Non-allowlisted attacker calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router calls pool.swap(recipient, ...) — msg.sender at pool = router.
5. Pool calls _beforeSwap(router, ...) → extension.beforeSwap(router, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Attacker's swap executes on the curated pool, bypassing the allowlist.
```

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
