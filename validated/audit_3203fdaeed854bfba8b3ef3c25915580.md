### Title
`SwapAllowlistExtension` gates on the router's address instead of the real user when swaps are routed through `MetricOmmSimpleRouter`, allowing any unprivileged user to bypass the per-pool swap allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (the natural step to let users swap via the router), the allowlist is completely bypassed for every user on Earth.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — The router is the direct caller of `pool.swap()`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [2](#0-1) 

The original user's address is stored only in transient storage for the payment callback; it is never forwarded to the pool as `sender`. The pool therefore sees `msg.sender = router`.

**Step 3 — The extension checks the wrong identity.**

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When the router is the caller, `sender = router`. The check becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the only way to let users swap via the router), every user on the network passes the check regardless of whether they are individually allowlisted.

**The invariant broken:** `allowedSwapper[pool][user]` is supposed to gate the economic actor initiating the swap. After routing through `MetricOmmSimpleRouter`, the gated identity is the router contract, not the user.

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted protocols). Once the pool admin allowlists the router to enable normal UX, the allowlist is rendered completely inoperative: any address can call `router.exactInputSingle()` and swap against the restricted pool. This exposes LP assets to unauthorized counterparties, potentially draining liquidity at oracle-derived prices that were only intended for vetted participants. The loss is direct and unbounded — the full pool liquidity is accessible to any caller.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user with tokens can call `MetricOmmSimpleRouter.exactInputSingle()`. The only precondition is that the pool admin has allowlisted the router, which is the expected operational step to make the pool usable via the standard periphery. The bypass is therefore reachable in every realistic deployment of a swap-allowlisted pool that also supports router-mediated swaps.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the real initiator through the router.** The router already stores `msg.sender` in transient storage for the payment callback. It should also forward it to the pool as an additional field (e.g., via `extensionData` or a dedicated `initiator` argument), and the pool should pass that value as `sender` to extensions when the direct caller is a known router.

2. **Alternatively, gate on `recipient` or require direct calls.** If the pool's design intent is that only allowlisted addresses may receive swap output, gating on `recipient` (which the user controls and which the router forwards correctly) would be more robust. However, this changes the semantics of the allowlist.

The simplest safe fix is to have the router encode `msg.sender` inside `extensionData` and have the `SwapAllowlistExtension` decode and verify it when the direct caller is a recognized router, or to require that allowlisted pools are only called directly (not via the router).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (extension1 = swapExt, beforeSwap order = 1)
  - Pool admin calls swapExt.setAllowedToSwap(pool, router, true)   // enable router UX
  - Pool admin does NOT call swapExt.setAllowedToSwap(pool, attacker, true)
  - Pool admin adds liquidity

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: X,
        recipient: attacker,
        ...
    })

  Execution path:
    router.exactInputSingle()
      → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, attacker receives token1

Result:
  Attacker successfully swaps on a pool they are not allowlisted for.
  The allowlist invariant is broken; LP assets are exposed to unauthorized counterparties.
```

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
