### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on curated pools to specific, admin-approved addresses. However, when a swap is routed through `MetricOmmSimpleRouter`, the extension receives the **router's address** as `sender` instead of the actual end-user. A pool admin who allowlists the router (the natural step to enable standard periphery usage) inadvertently grants every user on-chain the ability to bypass the per-user allowlist entirely.

---

### Finding Description

**Actor binding in the pool's `swap()` function:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

**Extension check:**

`SwapAllowlistExtension.beforeSwap()` gates on `sender`:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct), and `sender` is whatever `msg.sender` was when `pool.swap()` was called.

**Router path:**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    ...
);
```

When the router calls `pool.swap()`, `msg.sender` inside the pool is the **router**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The invariant break:**

The pool admin intends to allowlist specific end-users. To also allow those users to use the standard periphery (the router), the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller who routes through the router — the per-user allowlist is completely defeated.

The same actor-mismatch applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput` paths, all of which call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to approved counterparties loses that protection entirely once the router is allowlisted. Any unpermissioned user can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`, causing direct loss of LP assets through unauthorized trades at oracle-derived prices. This is a **High** impact: broken core pool functionality and curation failure allowing disallowed users to trade.

---

### Likelihood Explanation

Likelihood is **High**. The router is the canonical, documented periphery entry point. Any pool admin who wants their allowlisted users to be able to use the standard swap interface must allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any address can call `router.exactInputSingle()`.

---

### Recommendation

Pass the true end-user identity through the swap path so the extension can gate on it. Two concrete options:

1. **Preferred — add a `payer` field to the swap call**: Have the pool accept an explicit `payer` address (set by the router from `msg.sender` before the call) and pass it as `sender` to extensions instead of `msg.sender`.

2. **Alternative — check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the end-user, the extension could gate on `recipient`. However, this is semantically weaker (recipient ≠ payer in exact-output flows).

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router so Alice can use the periphery: `setAllowedToSwap(pool, router, true)`.
4. Bob (never allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, bob, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → **true** → swap proceeds.
8. Bob successfully trades against the curated pool despite never being individually approved.

**Relevant code locations:**

- Pool passes `msg.sender` as `sender` to extension: [1](#0-0) 
- Extension gates on `sender` (the router address in this path): [2](#0-1) 
- Router calls `pool.swap()` with itself as `msg.sender`: [3](#0-2) 
- Multi-hop `exactInput` has the same issue for intermediate hops: [4](#0-3) 
- Extension calling plumbing confirms `sender` = `msg.sender` of pool: [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
