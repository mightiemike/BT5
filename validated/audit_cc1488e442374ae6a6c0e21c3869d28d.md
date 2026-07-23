Now I have all the information needed. Let me trace the exact call path:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`
2. When called via router, `msg.sender` = router address
3. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

This is the wrong-actor binding: the extension gates the router address, not the end user.

---

Audit Report

## Title
Swap Allowlist Checks Router Address Instead of End User, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` inside `MetricOmmPool.swap` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, that immediate caller is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (required for any router-mediated swap to succeed), every unprivileged user can bypass the curated pool's allowlist by routing through the router.

## Finding Description

**Root cause — pool passes `msg.sender`, not the originating user:**

`MetricOmmPool.swap` (line 230–240):
```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

**Extension checks that router address:**

`SwapAllowlistExtension.beforeSwap` (line 37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct)
// sender     = router (wrong — should be end user)
```

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` (line 71–80):
```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    ...
);
// pool.swap sees msg.sender = router
```

**Exploit flow:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific KYC'd addresses.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router UI (without this, even allowlisted users cannot use the router).
3. Any non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`, the extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. The attacker successfully swaps on a pool they are not authorized to access.

**Existing guards are insufficient:** The `onlyPool` modifier in `BaseMetricExtension` only verifies the caller is the pool — it does not recover the originating user. There is no mechanism in the extension or the pool to propagate the true end-user identity through the router hop.

## Impact Explanation
A curated pool's swap allowlist is completely bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The pool admin's intent to restrict swaps to specific addresses is defeated. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or rate-limited liquidity), this allows unauthorized parties to drain LP liquidity at oracle-quoted prices, constituting a direct loss of LP assets and a broken core pool functionality (allowlist-gated swap flow). Severity: **High**.

## Likelihood Explanation
The condition is trivially reachable: any public user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The only precondition is that the router is allowlisted on the target pool, which is a necessary operational step for any pool admin who wants their allowlisted users to use the router. The attack requires no special privileges, no flash loans, and is repeatable every block.

## Recommendation
The extension must check the true originating user, not the immediate pool caller. Two viable fixes:

1. **Pass originating user via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trust assumption that the pool only accepts extension data from trusted routers.
2. **Check `recipient` instead of `sender`:** For swap allowlists, gate on `recipient` (the address receiving tokens) rather than `sender`. The router always forwards the user-supplied `params.recipient`, so this correctly identifies the economic beneficiary. This is the simpler fix and matches the semantic intent of "who is allowed to receive a swap output from this pool."
3. **Allowlist the router with a separate per-user check:** Require the router to forward the caller's address in a signed payload that the extension verifies on-chain.

The simplest correct fix is option 2: change line 37 of `SwapAllowlistExtension.sol` to gate on the `recipient` argument (second parameter) rather than `sender` (first parameter).

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Allowlist router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Do NOT allowlist attacker: allowedSwapper[pool][attacker] == false

// Attack:
// attacker (non-allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: curatedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// pool.swap sees msg.sender = router → extension checks allowedSwapper[pool][router] = true → PASSES
// attacker swaps successfully on a pool they are not authorized to access
```

Foundry test: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only the router, assert that a non-allowlisted EOA calling `exactInputSingle` succeeds (demonstrating the bypass), then assert the same EOA calling `pool.swap` directly reverts with `NotAllowedToSwap`.