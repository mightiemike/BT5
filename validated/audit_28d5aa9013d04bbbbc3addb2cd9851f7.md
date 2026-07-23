Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the curated allowlist entirely by routing through the public router.

## Finding Description

**Call path:**

1. Unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — at this point `msg.sender` seen by the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` passing the router as `sender`.
4. `ExtensionCalling._beforeSwap()` encodes and forwards `sender = router` to the extension.
5. `SwapAllowlistExtension.beforeSwap(address sender, ...)` evaluates:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
       revert NotAllowedToSwap();
   }
   ```
   Here `msg.sender` = pool (correct), but `sender` = **router**, not the original user.

**Root cause:** The extension checks `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the natural step to allow users to swap via the supported periphery path), the check passes for every caller regardless of their individual allowlist status.

**Existing guards are insufficient:** `_requireExpectedCallbackCaller` in the router only validates that the callback comes from a known pool; it does not propagate the original user identity into the pool's `swap()` call. There is no mechanism in the current code to pass the original `msg.sender` of the router call through to the extension.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses is fully bypassed: any unprivileged user calls the public router, the router is the `sender` seen by the extension, and if the router is allowlisted the check passes unconditionally. This constitutes a direct allowlist bypass on a core pool action (swap), matching the "allowlist bypass" allowed impact: broken core pool functionality and policy failure on curated pools. Severity: **High**.

## Likelihood Explanation
The attack requires no special privileges. Any user with access to the public `MetricOmmSimpleRouter` can execute it. The only precondition is that the pool admin has allowlisted the router (a natural and expected configuration step for pools that want to support the standard periphery). The attack is repeatable every block with no cooldown.

## Recommendation
The extension must resolve the original user identity rather than the immediate pool caller. Two options:

1. **Pass original sender through the router:** Have the router encode the original `msg.sender` inside `extensionData` and have the extension decode and verify it. This requires a trusted forwarding convention.
2. **Use `tx.origin` as a fallback (not recommended for general use):** Only acceptable in narrow, non-contract-user contexts.
3. **Preferred — store original sender in transient storage:** The router already uses transient storage (`TransientCallbackPool`) for callback context. Extend it to store the original `msg.sender` and expose a read function that the extension can call back into the router to retrieve the true initiator, then check that address against the allowlist.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; router is allowlisted, attacker is not.
// pool admin calls:
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker (not individually allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Result: swap succeeds; allowlist is bypassed because sender == router (allowlisted).
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router, call `exactInputSingle` from an address not in the allowlist, assert no revert.