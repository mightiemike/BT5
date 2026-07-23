### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass a Per-User Swap Allowlist via the Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. A pool admin who allowlists the router to enable router-based swaps for their curated users simultaneously opens the pool to every user on the router, completely defeating the per-user allowlist.

---

### Finding Description

**Root cause — wrong actor bound to the allowlist check.**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L231
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist, where `msg.sender` inside the extension is the pool:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly with no forwarding of the original caller:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

So when any user calls the router, `sender` delivered to the extension is the **router address**, not the user's address.

**The bypass path:**

A pool admin who wants to allow users A, B, C to swap via the router must allowlist the router address. But `allowedSwapper[pool][router] = true` means the extension passes for **every** caller of the router — including users D, E, F who were never meant to be allowed. There is no way to simultaneously allow A, B, C through the router while blocking D, E, F, because the router is a single shared address.

---

### Impact Explanation

**High — direct curation failure on allowlisted pools.**

The `SwapAllowlistExtension` is a production extension explicitly designed to restrict which addresses may trade on a curated pool. If the router is allowlisted (the only way to let any user use the router), the allowlist is completely bypassed for all router-mediated swaps. Any user can trade on a pool that was intended to be restricted, potentially:

- Draining LP liquidity at oracle-quoted prices that the pool admin intended only for specific counterparties.
- Executing swaps that the pool admin's risk model assumed would be limited to a known set of actors.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who deploys a swap-allowlisted pool and wants their allowlisted users to be able to use the router (the normal UX path) will naturally allowlist the router address. The bypass is then immediately active for all users. No special attacker setup is required — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary. Two approaches:

1. **Pass the original caller through the router.** The router stores `msg.sender` in transient storage (already done for the payer context). Extend this to pass the original user as `extensionData` or a dedicated transient slot, and have the extension read it when `msg.sender` is a known router.

2. **Check `recipient` instead of `sender` for swap allowlists**, or require the pool admin to allowlist individual users and document that the router cannot be used with per-user allowlists (breaking UX).

3. **Preferred:** Redesign `beforeSwap` to receive the original initiator address as a separate argument from the pool, analogous to how `beforeAddLiquidity` correctly passes `owner` (the economically relevant actor) separately from `sender` (the payer/operator).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, userA, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)  ← required for userA to use the router

Attack:
  - userB (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) → msg.sender = router
  - beforeSwap receives sender = router
  - allowedSwapper[pool][router] == true → check passes
  - userB's swap executes on the curated pool
  - userB was never allowlisted; the per-user gate is bypassed
```

**Trace:** [1](#0-0) 

`pool.swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 

The router calls `pool.swap()` with no forwarding of the original user's address. [3](#0-2) 

The extension checks `sender` (router address) against the allowlist — not the actual user. [4](#0-3) 

For contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position owner), which is the economically relevant actor regardless of who the payer/operator is.

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
