Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the allowlist checks the router's address rather than the end user's address. If the router is allowlisted — a natural admin action to permit router-mediated swaps — every unprivileged user can bypass the allowlist entirely and swap against a pool designed to restrict access.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (correct). `sender` is the first argument forwarded by `MetricOmmPool.swap`, which passes its own `msg.sender` — whoever called `pool.swap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`, and passes `""` for `callbackData` and the user-supplied `params.extensionData` — no originating user identity is forwarded:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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

The extension therefore receives `sender = router address`. The allowlist check becomes `allowedSwapper[pool][router]`. If the router is allowlisted, every user who calls through the router bypasses the per-user gate. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

`DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position recipient), which is correctly preserved through the liquidity adder path. The swap path has no equivalent end-user identity forwarding.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` in restrictive mode (e.g., a private institutional pool, a KYC-gated pool, or a pool restricted to specific counterparties) relies on the allowlist as its primary access control for swaps. If the pool admin allowlists the router — a natural action to permit allowlisted users to swap via the standard periphery — the allowlist is rendered ineffective: any unprivileged user can call `router.exactInputSingle()` and the hook will pass because it sees the router address, not the user. Unauthorized users can execute swaps the pool was designed to prohibit, directly extracting LP value through trades that should have been blocked. This constitutes a direct loss of LP principal and broken core pool access-control functionality.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the `MetricOmmSimpleRouter`. This is a natural, non-malicious configuration: a pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. Once that configuration is in place, any unprivileged user can exploit it with a single public call to the router. No special privileges, flash loans, or unusual tokens are required. The exploit is repeatable indefinitely until the router is de-allowlisted.

## Recommendation
The `beforeSwap` hook should gate the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two options:

1. **Require the router to forward the originating user in `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. This requires a coordinated change to the router and extension but preserves router usability for restricted pools.

2. **Remove the router from the allowlist and require allowlisted users to call `pool.swap()` directly.** This is the simplest fix but breaks router usability for restricted pools.

The cleanest production fix is option 1: define a convention where the router encodes the originating user in `extensionData`, and the `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - allowAllSwappers[pool] = false
  - allowedSwapper[pool][alice] = true        // alice is the only allowed swapper
  - allowedSwapper[pool][router] = true       // admin allowlists router so alice can use it

Attack:
  1. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: pool,
           recipient: bob,
           zeroForOne: true,
           amountIn: X,
           ...
       }))
  2. Router calls pool.swap() — router is msg.sender
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true → passes
  5. Bob's swap executes against the restricted pool
  6. Bob extracts value the pool admin intended to reserve for alice only

Foundry test outline:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, alice, true)
  - setAllowedToSwap(pool, router, true)
  - vm.prank(bob); router.exactInputSingle(...) → expect success (no revert)
  - Verify bob received output tokens despite not being on the allowlist
```