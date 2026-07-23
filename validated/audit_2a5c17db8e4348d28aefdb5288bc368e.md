The code confirms all three key facts in the claim. Let me verify the exact lines:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` at L230-231.
2. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` at L37, where `sender` is the immediate pool caller.
3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly at L72-80, passing `""` (empty bytes) as `callbackData` and `params.extensionData` (user-supplied) — the router never encodes the real `msg.sender` into `extensionData`.

The bypass path is real and requires no special privileges beyond the admin having allowlisted the router.

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` at the pool level. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract address, not the end-user. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, every user of the public router bypasses the allowlist entirely, allowing unauthorized swaps against a restricted pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap` (L230–231), which forwards it to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` (L37), where `msg.sender` inside the extension is the pool and `sender` is the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (L72–80) calls `pool.swap()`, the pool's `msg.sender` is `address(router)`, so the extension receives `sender = address(router)`. The router passes `""` as `callbackData` and user-supplied `params.extensionData` — it never encodes the real end-user's address into `extensionData`. There is no mechanism in the extension to recover the original caller. The pool admin faces an impossible choice: allowlisting the router grants all router users access; not allowlisting it blocks allowlisted users from using the router entirely.

## Impact Explanation
A pool deployer uses `SwapAllowlistExtension` to restrict trading to a curated set of counterparties. To allow those counterparties to use the standard router, the admin adds `address(router)` to the allowlist. At that point, any unprivileged address can call `MetricOmmSimpleRouter` and execute swaps against the restricted pool at oracle-determined prices without authorization. The allowlist access control is completely neutralized, constituting a broken core pool functionality and direct loss of LP assets to unauthorized traders.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, public, permissionless swap interface. Any user who observes that a pool has a `SwapAllowlistExtension` and that the router is allowlisted can immediately exploit this with a single `exactInputSingle` call. No special privileges, flash loans, or multi-step setup are required. The condition (admin allowlisting the router) is the natural and expected admin action when deploying an allowlisted pool intended to support router usage.

## Recommendation
The extension must gate on the end-user identity, not the immediate pool caller. The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify that address instead of trusting the `sender` argument. This requires either a trusted router registry in the extension (so it knows which callers are transparent forwarders) or a signed payload. Alternatively, document that pools using `SwapAllowlistExtension` must never allowlist the router and that allowlisted users must call `pool.swap()` directly — but this is a fragile usage restriction, not a code fix.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  2. Pool admin calls setAllowedToSwap(pool, address(router), true)
     (intended to allow allowlisted users to use the router)
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: restrictedPool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         extensionData: ""
     })
  2. Router calls restrictedPool.swap(attacker, true, X, priceLimitX64, "", "")
     → pool's msg.sender = address(router)
  3. _beforeSwap(address(router), ...) is called
  4. SwapAllowlistExtension.beforeSwap receives sender = address(router)
  5. allowedSwapper[pool][router] == true → check passes
  6. Swap executes; attacker receives output tokens

Result: attacker, who is not on the allowlist, successfully swaps against
        the restricted pool, bypassing the intended access control.
```