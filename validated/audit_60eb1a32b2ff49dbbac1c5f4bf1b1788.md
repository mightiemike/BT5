Audit Report

## Title
SwapAllowlistExtension Bypass via Router: Per-User Swap Gate Checks Router Identity Instead of Actual Swapper — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every address on the network, completely defeating the per-user allowlist.

## Finding Description

**Root cause — wrong identity checked in the hook:**

`SwapAllowlistExtension.beforeSwap` at line 37:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool (correct). `sender` is the first argument forwarded by `MetricOmmPool.swap()` at lines 230–240:
```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

**How the router breaks the check:**

`MetricOmmSimpleRouter.exactInputSingle` (lines 71–80) calls the pool directly:
```solidity
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
The router is `msg.sender` of `pool.swap()`, so the pool passes `address(router)` as `sender` to `_beforeSwap`. The extension then evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a whitelist of addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for their permitted users.
3. Any unprivileged address calls `MetricOmmSimpleRouter.exactInputSingle` targeting that pool.
4. The router calls `pool.swap()` — pool passes `router` as `sender` to the extension.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes. The unprivileged user swaps successfully.

**Existing guards are insufficient:** The extension has no mechanism to unwrap the actual initiating user from `extensionData` or any other source. The `sender` argument is structurally bound to `msg.sender` of `pool.swap()`, which is always the router when routing through `MetricOmmSimpleRouter`. There is no secondary check on the true originator.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control primitive for permissioned pools. Bypassing it allows unauthorized traders to execute swaps in pools that should be restricted — directly enabling fund flows that the pool admin explicitly prohibited. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's allowlist configuration is rendered ineffective by any user routing through the protocol's own router. The wrong value is the extension decision (`allowedSwapper[pool][sender]` resolves `true` for the router when it should resolve `false` for the unprivileged caller).

## Likelihood Explanation
The attack requires only that a pool admin has allowlisted the router address — a natural and expected action for any pool that intends to support router-mediated swaps. Once that condition holds, any unprivileged address can exploit it with a single `exactInputSingle` call. No special privileges, tokens beyond the swap input, or off-chain coordination are required. The condition is repeatable and permanent until the admin removes the router from the allowlist (which would also break legitimate router usage).

## Recommendation
The extension must identify the true initiating user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user via `extensionData`:** Have the router encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it instead of (or in addition to) `sender`.
2. **Check both router and originator:** Require that when `sender` is a known router, the extension also verifies the originating user encoded in `extensionData` is allowlisted.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData`, and the extension decodes and checks the real user when `sender` is a recognized router address.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension; allowlist only `alice`.
// 2. Pool admin calls: extension.setAllowedToSwap(pool, router, true)
//    (intending to let alice use the router)
// 3. Bob (not allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    tokenIn: token0,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// 4. pool.swap() is called with msg.sender = router.
// 5. _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] == true → passes.
// 6. Bob's swap executes despite not being on the allowlist.
```
A Foundry integration test deploying the pool with the extension, allowlisting only the router, and asserting that an arbitrary EOA can successfully swap via the router reproduces this finding.