### Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the actual user, allowing any unprivileged caller to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so `sender` is the router's address — not the actual user's address. If the pool admin allowlists the router (the natural step to let allowlisted users trade through the standard periphery), every non-allowlisted user can bypass the allowlist by routing through the same router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that `sender` and forwards it to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender` against the per-pool allowlist.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct key for the pool-scoped mapping) and `sender` is whoever called `pool.swap()`.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender` to the pool.**

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The router does not forward the original `msg.sender` (the actual user) to the pool. The pool therefore sees `sender = address(router)`.

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.**

A pool admin who wants allowlisted users to be able to trade through the standard periphery router must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of who the actual user is. Any non-allowlisted attacker can call `MetricOmmSimpleRouter.exactInputSingle(...)` and the extension check passes because `sender` resolves to the allowlisted router address.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the standard periphery at all (broken core functionality).
- **Allowlist the router** → any user bypasses the allowlist entirely (complete policy bypass).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd market makers, whitelisted counterparties) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker can execute swaps against LP capital that was never intended to be accessible to them, draining value from LPs through arbitrage or adverse selection that the allowlist was designed to prevent. This is a direct loss of LP principal and a complete failure of the pool's core access-control invariant.

---

### Likelihood Explanation

The router is the canonical, documented periphery entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` and want their allowlisted users to be able to use the router will naturally add the router to the allowlist — this is the expected operational pattern. The bypass requires no special privileges, no flash loans, and no complex setup: any EOA can call `exactInputSingle` on the router pointing at the restricted pool.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **actual user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router should forward the original `msg.sender` as an explicit `sender` field in `extensionData`, and the extension should decode and verify it. This requires a convention between the router and the extension.

2. **Alternatively, gate on `recipient` instead of `sender` for the swap allowlist**, if the pool's intent is to restrict who receives output tokens. This is semantically different but avoids the intermediary problem.

3. **Document the incompatibility** clearly: pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly (implementing `IMetricOmmSwapCallback` themselves).

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes it with a known selector, falling back to the raw `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Pool P is deployed with SwapAllowlistExtension E configured.
  - Pool admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // router allowlisted so alice can use it
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ...) — router is msg.sender to the pool.
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[P][router] == true  ✓  → swap proceeds.
  5. bob successfully swaps on the restricted pool without being allowlisted.

Direct call (blocked correctly):
  1. bob calls P.swap(...) directly.
  2. Pool calls _beforeSwap(sender=bob, ...)
  3. Extension checks: allowedSwapper[P][bob] == false → NotAllowedToSwap() revert. ✓
```

The bypass is reachable through all router entry points: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
