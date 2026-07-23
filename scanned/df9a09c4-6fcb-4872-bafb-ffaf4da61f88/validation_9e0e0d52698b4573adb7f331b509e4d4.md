### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool always passes `msg.sender` as `sender`, which is the **router contract address** when users enter through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to enable router-mediated swaps for their curated users, every unpermissioned user can bypass the allowlist by routing through the same router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (used as the mapping key) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool always passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

`msg.sender` inside `pool.swap` is therefore the **router address**, not the end user. The extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`.

This creates an irreconcilable conflict for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for allowlisted users |
| **Allowlist the router** | Every user — allowlisted or not — can swap by routing through the router |

The only way to let allowlisted users use the router is to add the router to the allowlist, which silently opens the pool to all users and defeats the entire purpose of the extension.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties (e.g., KYC'd users, institutional partners, or protocol-controlled addresses). Once the router is allowlisted — a natural and expected operational step — any unpermissioned user can execute swaps on the curated pool by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on `MetricOmmSimpleRouter`. This results in:

- Unauthorized users draining LP assets at oracle-derived prices from a pool that was designed to serve only specific counterparties.
- Direct loss of LP principal if the pool's pricing or liquidity was calibrated for a restricted set of actors.
- Complete curation failure: the allowlist provides no protection on the primary user-facing swap path.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router address. This is a natural operational step — the router is the primary user-facing interface and allowlisted users need it to swap. A pool admin who wants to allow their curated users to use the router will inevitably add the router to the allowlist, inadvertently opening the pool to all users. The admin action is semi-trusted but is the expected and documented usage pattern.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the end user — not the intermediary. Two approaches:

1. **Pass the original caller through the router**: Have the router encode the real `msg.sender` in `extensionData` and have the extension decode and check it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `sender` only for direct pool calls; require the router to forward the real user**: Add a dedicated field (e.g., `realSender`) to the extension data that the router populates with its own `msg.sender`, and have the extension prefer that field over the `sender` argument when present.

3. **Allowlist the router separately from users and enforce user identity inside the router**: The router could carry a per-user allowlist check before calling the pool, but this moves the guard off-chain and out of the extension framework.

The cleanest fix is option 1 or 2: the extension should read the true end-user identity from a router-signed field in `extensionData` rather than trusting the `sender` argument, which is always the immediate caller of `pool.swap`.

---

### Proof of Concept

**Setup:**
1. Deploy a pool with `SwapAllowlistExtension` wired as the `beforeSwap` hook.
2. Pool admin allowlists `alice` as a permitted swapper: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router so that `alice` can use it: `setAllowedToSwap(pool, router, true)`.

**Attack:**
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is `router`.
6. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps on the curated pool despite never being allowlisted.

**Key lines:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
