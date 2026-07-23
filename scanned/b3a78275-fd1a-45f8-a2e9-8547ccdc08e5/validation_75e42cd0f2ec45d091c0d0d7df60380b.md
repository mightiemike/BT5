### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any user to bypass the swap allowlist through `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` parameter in `beforeSwap`, which equals `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the `sender` delivered to the hook is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every user — including those not on the allowlist — can bypass the guard by routing through the same router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol lines 31-41
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

Here `msg.sender` is the pool (the only caller of this hook) and `sender` is the value forwarded by `MetricOmmPool.swap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol lines 71-80
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

So `sender` delivered to `beforeSwap` is the **router address**, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This forces the pool admin into an impossible choice:

| Admin configuration | Effect |
|---|---|
| Allowlist specific EOAs only, **not** the router | Allowlisted users **cannot** use the router; pool is unusable via standard periphery |
| Allowlist the router to enable router-mediated swaps | **Every** user can bypass the allowlist through the router |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users while blocking non-allowlisted users.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural step to make the pool usable via the standard periphery) inadvertently opens the pool to all users. Any non-allowlisted user can trade on the pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This breaks the core invariant of the allowlist guard and exposes LP funds to unauthorized counterparties who may exploit stale oracle prices, trigger stop-loss conditions, or drain liquidity through arbitrage — constituting a direct loss of LP principal above contest thresholds.

---

### Likelihood Explanation

The trigger is fully unprivileged: any user who observes that a pool has a `SwapAllowlistExtension` and that the router is allowlisted can immediately bypass the guard. The pool admin's natural operational step — allowlisting the router so that legitimate users can interact via the standard periphery — is precisely the action that opens the bypass. No special knowledge, flash loan, or privileged access is required.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **originating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and checks that address. This requires a coordinated change between the router and the extension.

2. **Check `sender` only for direct (non-router) calls; for router calls, require the router to attest the real user**: The extension reads a trusted field from `extensionData` when `sender` is a known router address, falling back to `sender` for direct calls.

Either approach must be validated so that a user cannot forge the attested address.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (extension1)
  pool admin calls: setAllowedToSwap(pool, alice, true)
  pool admin calls: setAllowedToSwap(pool, router, true)   ← to enable router swaps for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
        msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true   ← router is allowlisted
            → returns selector (no revert)
      → swap executes
      → bob receives output tokens

Result: bob trades on a curated pool without being on the allowlist.
        LP funds are exposed to an unauthorized counterparty.
```