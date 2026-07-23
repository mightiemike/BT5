### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist Guard — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap()` receives the **router** as `msg.sender` and forwards that address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual end-user. If the router is allowlisted (the natural operational choice for a pool that wants to accept router-mediated trades), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap()` calls:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — Router calls `pool.swap()` directly, making itself `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle()`:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [2](#0-1) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 3 — Extension checks the router address, not the user.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = router. The lookup is `allowedSwapper[pool][router]`. The actual end-user's address is never consulted.

**The structural mismatch:** The allowlist is keyed by `(pool, swapper)` and is intended to gate individual users. But the `sender` argument the extension receives is the immediate caller of `pool.swap()`, which is the router — not the user. The user's address is never passed to the extension at all.

---

### Impact Explanation

**Direct loss of curation / access-control integrity on allowlisted pools.**

A pool admin deploys a curated pool (e.g., institutional-only, KYC-gated, or whitelist-only) and attaches `SwapAllowlistExtension`. To allow normal router-mediated trading for approved users, the admin allowlists the router address (`setAllowedToSwap(pool, router, true)`). From that moment, **every address on the network** can trade on the pool by calling any router function — the allowlist is completely inoperative. Non-approved users receive the same execution as approved ones, draining LP value under conditions the pool admin explicitly prohibited.

Even if the admin does not allowlist the router, approved users who try to use the router are blocked (the router is not in the allowlist), forcing them to interact directly with the pool and implement their own swap callback — a broken UX that defeats the purpose of the periphery.

Severity: **High** — the allowlist guard, a core protection mechanism, is silently bypassed for all router-mediated swaps on any pool where the router is allowlisted.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Pool admins who want to allow approved users to trade via the router must allowlist the router address. This is the expected operational pattern. The bypass is therefore reachable on every production pool that uses `SwapAllowlistExtension` and accepts router-mediated swaps — which is the common case.

No special permissions, malicious setup, or non-standard tokens are required. Any EOA can call `exactInputSingle` on the router pointing at the guarded pool.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end-user — not the immediate caller of `pool.swap()`. Two options:

1. **Pass the original user through the router.** Add a `payer` / `originator` field to the swap call or use transient storage (already used for reentrancy) so the router records `msg.sender` before calling the pool, and the pool forwards it as a separate `originator` argument to the extension.

2. **Check `recipient` instead of `sender` for the allowlist.** If the pool's design guarantees that the recipient is always the end-user, the extension can gate on `recipient`. However, this is fragile if recipient can be set to a third party.

3. **Require direct pool interaction for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert in `beforeSwap` when `sender` is a known router address. This is the simplest safe fix but limits composability.

The cleanest fix is option 1: thread the original `msg.sender` from the router through to the extension as a distinct `originator` field.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwap hook enabled)
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. attacker (non-allowlisted EOA) calls:
       router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes successfully for the non-allowlisted attacker

Result:
  - Non-allowlisted user completes a swap on a curated pool
  - SwapAllowlistExtension guard is fully bypassed
  - Pool admin's access-control policy is silently violated
``` [4](#0-3) [5](#0-4) [6](#0-5)

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
