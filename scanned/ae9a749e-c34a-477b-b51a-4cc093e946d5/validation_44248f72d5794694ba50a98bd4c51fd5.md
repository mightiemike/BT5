### Title
`SwapAllowlistExtension` gates the router address instead of the actual user when swaps route through `MetricOmmSimpleRouter`, allowing any user to bypass the per-user allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. The allowlist therefore gates the router address, not the individual. A pool admin who allowlists the router to enable router-mediated swaps for approved users simultaneously opens the gate for every unapproved user who routes through the same contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `beforeSwap` hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the first argument) is allowlisted for the calling pool: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [3](#0-2) 

There is no mechanism in the router to forward the originating user's address to the pool. The pool only ever sees `msg.sender = router`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the per-user gate by routing through the router |

The admin cannot simultaneously permit router-mediated swaps for approved users and block unapproved users from using the same path.

---

### Impact Explanation

Any user who is not individually allowlisted can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting a pool that has `SwapAllowlistExtension` configured in its `beforeSwap` order. As long as the router address appears in `allowedSwapper[pool][router]` — a prerequisite for any allowlisted user to use the router — the hook passes unconditionally for the unapproved caller. The unapproved user executes a live swap against LP liquidity in a pool that was explicitly configured to restrict access, constituting a direct policy bypass with fund-impacting consequences (LP exposure to counterparties the pool admin intended to exclude).

---

### Likelihood Explanation

The scenario is reachable on any production pool that:
1. Deploys `SwapAllowlistExtension` in its `beforeSwap` order (a standard periphery extension).
2. Allowlists the router so that approved users can trade through the supported periphery path.

Both conditions are normal operational choices. No privileged attacker capability, malicious setup, or non-standard token is required. Any EOA can call the public router functions.

---

### Recommendation

The `sender` argument passed to `beforeSwap` should represent the economic actor, not the immediate caller. Two complementary fixes:

1. **In the router**: store the originating `msg.sender` in transient storage alongside the callback context and expose it via a standard interface so the pool (or extension) can read the true initiator.
2. **In `SwapAllowlistExtension`**: check `recipient` or a separately forwarded originator field rather than `sender` when the immediate caller is a known router, or require the router to pass the real user address through `extensionData` and verify it there.

Alternatively, document clearly that `SwapAllowlistExtension` gates the immediate pool caller (the router) and that pool admins must not allowlist the router if per-user restrictions are intended.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension in beforeSwap order
  pool admin: setAllowedToSwap(pool, alice, true)
  pool admin: setAllowedToSwap(pool, router, true)   // required for alice to use the router

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)   // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes, bob receives tokens

Result: bob swaps successfully in a pool that was supposed to block him.
``` [4](#0-3) [5](#0-4) [3](#0-2)

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
