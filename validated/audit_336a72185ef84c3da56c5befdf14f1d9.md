Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Enabling Complete Allowlist Bypass for Any User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the router's allowlist status rather than the actual user's. Any pool admin who allowlists the router (required for legitimate allowlisted users to use it) simultaneously grants every unpermissioned user the ability to bypass the allowlist by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The `bytes calldata` (extensionData) parameter is unnamed and entirely ignored — there is no mechanism to recover the real initiating user from it.

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool therefore passes `address(router)` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for every caller regardless of their own allowlist status. The `exactInput`, `exactOutputSingle`, and `exactOutput` entry points on the router exhibit the same behavior.

The dilemma is inescapable: not allowlisting the router blocks all allowlisted users from using it; allowlisting the router opens the pool to every user.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd users, institutional counterparties). Once the router is allowlisted, any unpermissioned address can execute swaps in the restricted pool by calling `router.exactInputSingle` or `router.exactInput`. The allowlist access-control invariant is completely broken for all router-mediated swaps, allowing unauthorized fund flows through the pool. This is a direct breach of the pool's core access-control guarantee and constitutes a broken core pool functionality impact.

## Likelihood Explanation
The router is the primary user-facing swap interface. Any pool admin who wants allowlisted users to use the standard router must allowlist the router address — this is a natural and expected administrative action. Once done, the bypass requires no special tokens, no privileged access, and no additional preconditions. Any address can exploit it immediately and repeatedly.

## Recommendation
The extension must identify the real initiating user, not the transport layer. Two approaches:

1. **Trusted router + `extensionData` forwarding**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension maintains a registry of trusted routers; when `msg.sender` (the pool's caller, i.e., the router) is a trusted router, it decodes and checks the real user from `extensionData`; otherwise it checks `sender` directly.

2. **Transient storage forwarding**: The router writes the real caller into transient storage before calling the pool; the extension reads it from a known slot. This avoids encoding overhead but requires a shared transient storage convention.

Either approach requires the extension to be aware of the router layer so it gates the economically relevant actor (the user), not the transport layer (the router).

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` (required for `userA` to use the router).
3. `userB` (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
4. The router calls `pool.swap(...)` with `msg.sender = address(router)`.
5. The pool calls `extension.beforeSwap(address(router), ...)`.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `userB` successfully swaps in a pool they were never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
