### Title
`SwapAllowlistExtension.beforeSwap()` checks the router address as `sender` instead of the actual user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on curated pools to a specific set of allowlisted addresses. However, the extension checks `sender` — which the pool sets to `msg.sender` of the `swap()` call — rather than the actual end user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the user. This creates an irreconcilable dilemma: either the router is allowlisted (opening the pool to every user), or it is not (blocking all router-based swaps even for individually allowlisted users). An unprivileged user can exploit the former configuration to bypass the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the direct caller of swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(   // ← router is msg.sender to the pool
    params.recipient, params.zeroForOne, ...
);
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants to support router-based swaps must allowlist the router address. Once the router is allowlisted, **every** user — including those explicitly not on the allowlist — can swap through the router without restriction.

The same structural problem exists for `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`, all of which call `pool.swap()` with the router as `msg.sender`. [4](#0-3) 

---

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses loses that guarantee entirely once the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against the pool. This constitutes a direct policy bypass on a production extension whose sole purpose is access control, and can result in unauthorized parties draining liquidity or executing trades the pool operator explicitly intended to block.

**Severity: Medium** — the bypass requires the pool admin to have allowlisted the router (a reasonable operational step), but once that configuration exists the bypass is unconditional and requires no further privilege.

---

### Likelihood Explanation

Any pool that uses `SwapAllowlistExtension` and also wants users to interact via the standard `MetricOmmSimpleRouter` periphery must allowlist the router. This is the expected operational path for most integrators. The moment the router is allowlisted, the allowlist is effectively nullified for all router-routed swaps. The trigger is a normal, documented, supported user flow.

---

### Recommendation

The extension must be able to identify the true economic actor, not the intermediary. Two complementary approaches:

1. **Pass the real user through `extensionData`**: The router encodes the actual `msg.sender` (user) into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a trusted encoding convention between the router and the extension.

2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists, the recipient is often the meaningful actor. The extension signature already receives `recipient` as the second argument (currently ignored with `address`). Gating on `recipient` is not a complete fix (recipient can be a third party), but combined with approach 1 it closes the gap.

3. **Allowlist the router separately from individual users and enforce user-level checks inside the router**: The router itself enforces a per-user allowlist before calling the pool, and the pool-level extension only checks whether the caller is a trusted router. This moves enforcement to the periphery, which is weaker but avoids the actor-mismatch.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to allow router-based swaps for `alice`.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. `bob`'s swap executes successfully despite never being allowlisted. [5](#0-4) [1](#0-0) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
