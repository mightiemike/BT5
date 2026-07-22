### Title
SwapAllowlistExtension checks the router address instead of the actual user, allowing any user to bypass per-user swap gating via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is the intermediary, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (the only way to let any user swap through it), every user bypasses the per-user allowlist. If the admin allowlists individual users instead, those users are locked out of the router entirely.

---

### Finding Description

**Actor binding in the pool's `swap` function:**

The pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
``` [1](#0-0) 

**The extension checks that `sender`:**

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

**The router calls the pool directly, losing the user's identity:**

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,   // ← token recipient, not the user identity
    params.zeroForOne,
    ...
    params.extensionData
  );
``` [3](#0-2) 

The actual user (`msg.sender` of the router call) is stored only in transient callback context for payment purposes and is **never forwarded to the pool or the extension**. The pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` — the explicit position-owner address that `MetricOmmPoolLiquidityAdder` passes directly to `pool.addLiquidity(owner, ...)`. The deposit path does not have this mismatch. [4](#0-3) 

---

### Impact Explanation

Two fund-impacting outcomes arise from the wrong actor binding:

1. **Allowlist bypass (High):** The pool admin must allowlist the router address to permit any user to swap through it. Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every user, regardless of whether that user is individually permitted. Any address — including addresses the admin explicitly never allowlisted — can trade in the curated pool by routing through `MetricOmmSimpleRouter`. This directly violates the curation invariant and allows unauthorized parties to drain or manipulate pool liquidity.

2. **Allowlist lockout (Medium):** If the admin allowlists individual user addresses (not the router), those users cannot swap through the router even though they are permitted. They must call `pool.swap()` directly, which requires implementing the `metricOmmSwapCallback` interface. This makes the router-based UX completely unusable for curated pools, breaking core swap functionality.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract explicitly designed for curated pools. Any pool that deploys it and also expects users to interact via `MetricOmmSimpleRouter` (the standard user-facing entrypoint) will encounter this mismatch on every swap. The trigger requires no special privileges: any user can call `exactInputSingle` or `exactInput` on the router.

---

### Recommendation

The extension must identify the actual initiating user. Two viable approaches:

1. **Pass the user via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated encoding convention.

2. **Check `recipient` as a proxy:** For single-hop swaps where the user is also the recipient, checking `recipient` instead of `sender` would be correct. However, `recipient` can be an arbitrary address in multi-hop or delegated flows, so this is only safe for single-hop, self-recipient swaps.

The cleanest fix is approach (1): the router always appends the initiating user's address to `extensionData`, and the extension reads it from there rather than from the `sender` parameter.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]`.
7. If the admin previously allowlisted the router (step 2b: `setAllowedToSwap(pool, router, true)`), Bob's swap succeeds — allowlist bypassed.
8. If the router is not allowlisted, Alice's swap also fails through the router — allowlisted user locked out.

The root cause is at: [5](#0-4) 

where `sender` is always the router when the standard periphery path is used, not the user the allowlist was configured to gate.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
