### Title
SwapAllowlistExtension gates the router contract address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address — a natural and expected action to let allowlisted users access multi-hop or slippage-protected swaps — every unprivileged user can bypass the per-user restriction by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to the configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, i.e. the router when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is whoever called the pool. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users (e.g., KYC'd addresses).
2. Allowlisted users want to use the router for slippage protection or multi-hop swaps. The pool admin allowlists the router address to enable this.
3. Any unprivileged user (not in the per-user allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The pool receives `msg.sender = router`. The extension checks `allowedSwapper[pool][router] = true`. The swap proceeds.
5. The per-user allowlist is completely bypassed.

This is structurally identical to the Superfluid analog: a guard (`unlockAvailable` / `SwapAllowlistExtension`) is missing from a fund-moving path (`withdrawLiquidity` / router-mediated swap), allowing users to act as if the guard does not exist.

The `DepositAllowlistExtension` does not share this flaw — it correctly checks `owner` (the position holder), ignoring `sender`, which matches the documented operator pattern. The `SwapAllowlistExtension` has no equivalent correct fallback: it must check the actual human swapper, but it receives the router address instead.

---

### Impact Explanation

Any user can swap against a pool that the admin intended to restrict to a specific allowlist, provided the router is allowlisted (which is the only way to let allowlisted users use the router). This breaks the core invariant of the `SwapAllowlistExtension`. Depending on pool configuration, non-allowlisted users can extract value from LPs by trading at oracle-anchored prices that were intended only for privileged counterparties, constituting a direct loss of LP assets.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected configuration step: without it, allowlisted users cannot use the router at all (their address is not the router). Any pool admin who deploys a swap-allowlisted pool and also wants allowlisted users to access the router will trigger this condition. The pool admin has no way to simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same, because the router is a shared contract with a single address.

---

### Recommendation

The extension must check the identity of the actual human swapper, not the intermediary contract. Two approaches:

1. **Check `sender` only for direct calls; require the router to forward the originating user.** Add a `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it. This requires a trusted router registry or a signed payload.

2. **Gate on `sender` and treat the router as a transparent forwarder.** Modify `MetricOmmSimpleRouter` to pass the originating user as the `recipient`-equivalent identity in `extensionData`, and update `SwapAllowlistExtension` to decode and check that field when `sender` is a known router.

3. **Simplest safe fix:** Document that allowlisting the router grants unrestricted access to all router users, and provide a separate `SwapAllowlistExtension` variant that checks `sender` only when `sender` is not a registered router, falling back to an `extensionData`-encoded user identity for router calls.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension (BEFORE_SWAP_ORDER set).
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: bob, ...})
  - Router calls pool.swap(bob, ...) → pool's msg.sender = router
  - Extension checks allowedSwapper[pool][router] = true → passes
  - Bob's swap executes against the restricted pool.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
