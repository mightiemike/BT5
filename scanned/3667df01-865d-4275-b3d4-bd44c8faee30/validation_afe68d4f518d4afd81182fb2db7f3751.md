Now I have all the pieces needed to trace the full call path. Let me verify the exact `sender` value passed through the chain.

The call chain is fully traceable and the vulnerability is real. Here is the determination:

---

### Title
SwapAllowlistExtension Checks Router Identity Instead of EOA — Allowlist Fully Bypassed via Router

**File:** `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, but the pool sets `sender = msg.sender` of its own `swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the **router address**, not the originating EOA. If the router is allowlisted, every EOA in the world can bypass the allowlist by routing through it.

---

### Finding Description

The call chain is:

```
EOA → MetricOmmSimpleRouter.exactInputSingle()
        └─ pool.swap(recipient, ...)          // msg.sender = router
              └─ _beforeSwap(msg.sender, ...) // sender = router
                    └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                          └─ allowedSwapper[pool][router] == true → PASS
```

**Step 1 — Router calls pool directly, no EOA identity forwarded:** [1](#0-0) 

The router calls `pool.swap()` with no mechanism to forward `msg.sender` (the EOA). The pool sees `msg.sender = router`.

**Step 2 — Pool passes its own `msg.sender` as `sender` to the extension:** [2](#0-1) 

`_beforeSwap(msg.sender, ...)` — `msg.sender` here is the router, not the EOA.

**Step 3 — Extension checks the router address, not the EOA:** [3](#0-2) 

`allowedSwapper[msg.sender][sender]` evaluates as `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes unconditionally for any EOA.

---

### Impact Explanation

The `SwapAllowlistExtension` is documented as gating `swap` by swapper address per pool. [4](#0-3)  The invariant it is supposed to enforce — that only allowlisted addresses can swap — is completely broken when any router is allowlisted. Any non-allowlisted EOA can execute swaps against the pool's LP balances and receive token output, defeating the entire purpose of the extension. This is broken core pool functionality: the access control layer that pool admins rely on to restrict participation is rendered ineffective.

---

### Likelihood Explanation

Allowlisting the official `MetricOmmSimpleRouter` is the natural and expected admin action for any pool that wants to permit allowlisted users to swap via the standard periphery. The admin has no on-chain signal that doing so opens the pool to all users. The precondition (router allowlisted) is the normal operating state for any pool using both the router and the allowlist extension together.

---

### Recommendation

The extension must check the economic originator, not the immediate caller. Two options:

1. **Pass `tx.origin` as an additional parameter** from the pool to the extension (breaks composability and is generally discouraged).
2. **Require swappers to call the pool directly** (not via router) when the allowlist extension is active — document this constraint clearly.
3. **Preferred:** Add a `swapper` field to `extensionData` that the router populates with `msg.sender`, and have the extension verify it against a signature or trusted-forwarder pattern. Alternatively, redesign the extension to check `recipient` rather than `sender` if the pool's intended semantic is "who receives the output."

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router (natural action)
ext.setAllowedToSwap(address(pool), address(router), true);

// Attack: non-allowlisted EOA swaps via router
address attacker = makeAddr("attacker"); // NOT in allowedSwapper
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    ...
    recipient: attacker
}));
// Swap succeeds; attacker receives token output
// attacker was never checked against allowedSwapper[pool][attacker]
```

The `beforeSwap` check evaluates `allowedSwapper[pool][router] == true` and passes, while `allowedSwapper[pool][attacker]` is never consulted.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-12)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
