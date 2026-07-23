The code confirms all claims. Let me verify the exact lines:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument — the direct caller of `pool.swap()`. [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`, so when the router calls the pool, `sender` = router address. [2](#0-1) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no user identity forwarded. [3](#0-2) 

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users trade via the router), the allowlist becomes completely ineffective for all router-mediated swaps, allowing any unprivileged user to bypass it.

## Finding Description
`SwapAllowlistExtension.beforeSwap` (L37) evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument passed by the pool. In `MetricOmmPool.swap` (L230–231), the pool passes its own `msg.sender` as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter.exactInputSingle` (L72–80) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The router passes `""` as `callbackData` and `params.extensionData` as `extensionData` — no user identity is forwarded. There is no existing guard that recovers the originating user's address. The pool admin faces an impossible configuration: allowlisting the router (required for allowlisted users to trade via the router) simultaneously grants every user the ability to bypass the per-user allowlist.

## Impact Explanation
Any non-allowlisted user can bypass a pool's swap allowlist by calling any public router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). If the pool admin has allowlisted the router, the allowlist is completely ineffective for router-mediated swaps. LP funds are exposed to trades from actors the pool admin explicitly intended to exclude, breaking the core curation invariant of the extension. This matches the "Allowlist path" audit pivot: swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through the router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. No special privileges, flash loans, or multi-step setup are required — a single public call to any router entry point suffices. Any user who discovers the pool has a swap allowlist can trivially route through the router. The bypass is reliable and repeatable.

## Recommendation
The extension must check the economic actor, not the intermediate contract. The simplest correct fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode it when present, falling back to `sender` for direct pool calls. Alternatively, the pool could expose a `swapOnBehalfOf(address realUser, ...)` entry point that passes `realUser` as `sender` to extensions, with the router using that entry point.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice may swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Charlie (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` to pool = router address.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Charlie's swap executes successfully, bypassing the allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
