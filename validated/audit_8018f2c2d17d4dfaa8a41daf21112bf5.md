### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any unprivileged swapper to bypass a curated pool's allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` as `sender`, so when a user enters through `MetricOmmSimpleRouter`, the extension sees the **router's address**, not the actual user. Any pool that allowlists the router to support router-mediated swaps simultaneously opens the gate to every unprivileged user.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Resulting call chain for a non-allowlisted user:**

```
User (blocked) → Router.exactInputSingle()
  → pool.swap()          [msg.sender = router]
    → _beforeSwap(sender = router, ...)
      → allowedSwapper[pool][router]  ← checked, NOT the user
```

For a curated pool to support router-mediated swaps at all, the admin must add the router to `allowedSwapper`. Once that entry exists, the check `allowedSwapper[pool][router]` passes for **every** caller who routes through the router, regardless of whether the actual user is on the allowlist.

---

### Impact Explanation

The allowlist is the sole access-control mechanism for curated pools. Bypassing it lets any unprivileged address execute swaps against a pool that was explicitly configured to restrict trading to a known set of counterparties. This is a direct policy bypass with fund-impacting consequences: the pool's LP providers accepted liquidity risk under the assumption that only vetted counterparties could trade against them.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point documented and deployed for all user swaps. Any curated pool that wants to remain usable through the standard periphery must allowlist the router. The bypass requires no special privileges, no flash loan, and no unusual token behavior — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must resolve the **real end-user** rather than the immediate caller. Two sound approaches:

1. **Check `tx.origin` as a secondary signal** — still imperfect (as the external report notes), but better than checking the router.
2. **Pass the payer/initiator through `extensionData`** — the router already stores `msg.sender` in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`); the extension could read a caller-supplied identity from `extensionData` and verify it matches the callback payer.
3. **Preferred:** Require the pool admin to allowlist individual users, and document that the router must **not** be added as a blanket allowlist entry. Alternatively, redesign the hook so the pool passes the transient-storage payer (already tracked by the router) as a dedicated `payer` argument to the extension, analogous to how `owner` is separated from `sender` on the liquidity path.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  - Alice (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap() → msg.sender to pool = router
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Alice's swap executes successfully despite not being on the allowlist.

Direct call (correctly blocked):
  - Alice calls pool.swap() directly
  - Extension checks allowedSwapper[pool][alice] → false  ✗  → revert NotAllowedToSwap
```

The asymmetry between the direct path and the router path is the root cause: the guard keys on the intermediary, not the economic actor.

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
