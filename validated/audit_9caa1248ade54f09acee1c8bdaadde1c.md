All four steps of the call chain are confirmed by the production code:

- `MetricOmmPool.sol` L230-231: `_beforeSwap(msg.sender, ...)` — pool passes its own `msg.sender` as `sender`. [1](#0-0) 
- `ExtensionCalling.sol` L162-165: `sender` is forwarded unchanged into `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`. [2](#0-1) 
- `SwapAllowlistExtension.sol` L37-39: checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap`. [3](#0-2) 
- `MetricOmmSimpleRouter.sol` L71-80: the router stores the end user only as `payer` in transient storage and calls `pool.swap(...)` directly — making the router the pool's `msg.sender`. [4](#0-3) 

---

Audit Report

## Title
Router Address Checked Instead of End User in `SwapAllowlistExtension::beforeSwap`, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` function. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, silently nullifying the per-user allowlist for all router-mediated flows.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:**
`MetricOmmPool::swap` calls `_beforeSwap(msg.sender, recipient, ...)` at L230–231. The value passed is whoever called `pool.swap`, not the originating user.

**Step 2 — `ExtensionCalling::_beforeSwap` forwards `sender` unchanged:**
At L162–165, `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` passes the value through without modification.

**Step 3 — Extension checks `allowedSwapper[pool][sender]`:**
At L37–39 of `SwapAllowlistExtension.sol`:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool; `sender` is whoever called `pool.swap`.

**Step 4 — Router is the direct caller of `pool.swap`:**
In `exactInputSingle` (L71–80), `MetricOmmSimpleRouter` stores the end user only as `payer` in transient storage via `_setNextCallbackContext`, then calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The same pattern applies in `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181). The end user's address never reaches the pool as `sender`.

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Existing guards are insufficient because the check is structurally correct but operates on the wrong address for all router-mediated paths.

## Impact Explanation

Two concrete failure modes:

1. **Allowlist bypass (security):** A pool admin who allowlists the router so that approved users can access it via the router inadvertently grants swap access to *any* address. Any non-allowlisted user can call `router.exactInputSingle(...)` and the extension will pass because `allowedSwapper[pool][router] == true`. The per-user gate is completely bypassed.

2. **Allowlisted users locked out of the router (broken core functionality):** If the admin does not allowlist the router, every allowlisted user who attempts a router-mediated swap is rejected. The router becomes unusable for any pool deploying `SwapAllowlistExtension`.

Both outcomes break the stated invariant of the extension: *"Gates `swap` by swapper address, per pool."*

## Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) allowlists the router to enable router access for its approved users is immediately exploitable. This is a natural and expected configuration — pool admins restricting swaps to known counterparties will also want those counterparties to use the standard router. The flaw is invisible from the admin interface: `setAllowedToSwap(pool, router, true)` is indistinguishable from allowlisting any other address. No special attacker capability is required beyond calling the public router.

## Recommendation

The extension must identify the true end user, not the immediate caller. The most contained fix: the router encodes the original `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes it and verifies the decoded address against the allowlist, using `msg.sender` (the pool) to confirm the pool's `msg.sender` is the trusted router before trusting the decoded value. Alternatively, document that the router must never be allowlisted and provide a separate extension variant that reads the payer from the router's transient context.

## Proof of Concept

```
// Setup
Pool configured with SwapAllowlistExtension.
allowedSwapper[pool][alice] = true   // Alice is the only approved swapper
allowedSwapper[pool][router] = true  // Admin adds router so Alice can use it

// Attack: Bob (not allowlisted) calls:
router.exactInputSingle({pool: pool, recipient: bob, ...})
// pool.swap is called with msg.sender = router
// beforeSwap receives sender = router
// allowedSwapper[pool][router] == true → check passes
// Bob's swap executes despite not being on the allowlist

// Direct swap by Bob (correctly blocked):
pool.swap(bob, ...)
// beforeSwap receives sender = bob
// allowedSwapper[pool][bob] == false → reverts ✓
```

The allowlist is enforced for direct calls but silently bypassed for all router-mediated calls once the router is allowlisted.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L162-165)
```text
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
