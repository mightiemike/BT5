Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct caller of `MetricOmmPool.swap`, which is the router contract address when swaps are routed through `MetricOmmSimpleRouter`. The allowlist check therefore gates the router address rather than the actual end user. Any pool admin who allowlists the router to enable router-based swaps for legitimate users simultaneously opens the pool to all users, completely defeating the curation purpose of the extension.

## Finding Description

**Exact call path:**

1. `MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly — `msg.sender` inside the pool is the **router address**.

2. `MetricOmmPool.swap` (line 230–240) calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.

3. `ExtensionCalling._beforeSwap` (line 160–176) encodes `sender` (router address) and dispatches to the configured extension.

4. `SwapAllowlistExtension.beforeSwap` (line 37) evaluates:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
   ```
   Here `msg.sender` = pool, `sender` = **router address**. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Root cause:** The `sender` parameter propagated through the extension system is always `msg.sender` of the pool's `swap` call. When the router intermediates, this is the router contract, not the originating EOA or contract.

**Exploit flow:**
- Pool is deployed with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses.
- Pool admin calls `setAllowedToSwap(pool, router, true)` to allow legitimate users to swap via the router.
- Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting this pool.
- The pool's `_beforeSwap` passes `sender = router` to the extension.
- The extension sees `allowedSwapper[pool][router] == true` and permits the swap.
- The non-allowlisted user successfully swaps on a curated pool.

**Existing guards are insufficient:** The `BaseMetricExtension.onlyPool` modifier only ensures the extension is called by a registered pool — it does not recover the original user. There is no mechanism in the extension or the pool to pass the originating `tx.origin` or a verified user identity through the router.

## Impact Explanation
Direct allowlist bypass on curated pools. Any unprivileged user can trade on a pool that the pool admin intended to restrict to a specific set of swappers. This constitutes broken core pool functionality (the allowlist gate fails open for all router-mediated swaps) and may cause direct loss of LP assets if the pool's curation was designed to prevent adverse selection or restrict access to specific counterparties.

## Likelihood Explanation
Exploitable by any unprivileged user with no special setup beyond knowing the router address and the pool address. The condition is met whenever a pool admin allowlists the router (a necessary step to support router-based swaps for any user). The attack is repeatable every block and requires no privileged access.

## Recommendation
The extension must check the economically relevant actor, not the immediate caller. Options:
1. Pass the originating user through the router as an explicit parameter and have the pool forward it to extensions as a separate `originator` field.
2. Have the router record the originating user in transient storage (similar to how it records the payer via `TransientCallbackPool`) and expose a read function that the extension can call back into to retrieve the real user.
3. Require that allowlisted pools are only accessed directly (document and enforce that the router is incompatible with `SwapAllowlistExtension`), but this breaks the intended periphery integration.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists the router: swapExtension.setAllowedToSwap(pool, router, true)
// 3. Non-allowlisted attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: curated_pool,
    tokenIn: token1,
    recipient: attacker,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: false,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
}));
// 4. Pool calls _beforeSwap(msg.sender=router, ...)
// 5. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
// 6. Attacker swaps successfully despite not being on the allowlist
```