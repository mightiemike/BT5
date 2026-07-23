### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If a pool admin allowlists the router address (a natural action to enable routing for any permitted user), every unpermitted user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension dispatcher.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with its own `msg.sender`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`.** [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call.** [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: in every case the router is the entity that calls `pool.swap()`, so `sender` delivered to the extension is always `address(router)`, never the originating user.

**The mismatch:** `DepositAllowlistExtension` correctly gates by `owner` (the position beneficiary), ignoring the operator/payer: [5](#0-4) 

`SwapAllowlistExtension` has no equivalent "actual user" field to inspect — it only receives `sender`, which collapses to the router for all router-mediated swaps.

---

### Impact Explanation

A pool admin who wants to allow routing (so that allowlisted users can use the router) must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** address — including addresses explicitly not on the allowlist — can call `router.exactInputSingle(...)` and the extension will pass, because it only checks `allowedSwapper[pool][router]`. The per-user curation is completely defeated. Funds flow through a pool that was designed to restrict access to a curated set of counterparties.

Conversely, if the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking the intended UX for legitimate participants.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery.
- Any pool admin enabling routing for curated pools will naturally allowlist the router.
- Once the router is allowlisted, the bypass requires zero privilege: any EOA calls `exactInputSingle` with the target pool.
- The bypass is silent — no revert, no event distinguishing the bypassing user from a legitimate one.

---

### Recommendation

The extension must be able to identify the originating user, not just the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and verifies it. This requires the extension to trust that the router is the `sender` and that the encoded address is authentic — which can be enforced by also checking `allowedSwapper[pool][sender]` (i.e., the router must itself be allowlisted, and the decoded user must also be allowlisted).

2. **Allowlist at the router level, not the pool level**: Deploy a router wrapper that enforces per-user access before calling the pool, and allowlist only that wrapper. The pool-level extension then only needs to check that the wrapper (a trusted intermediary) is the caller.

The core invariant to restore: **the identity checked by the allowlist must be the economically relevant actor — the user whose funds are at risk — not the intermediate contract routing the call.**

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured (beforeSwap order = extension 1).
  - Admin calls setAllowedToSwap(pool, router, true)   // enable routing
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls router.exactInputSingle({
    pool:      <curated pool>,
    recipient: attacker,
    zeroForOne: true,
    amountIn:  X,
    ...
  })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...)   // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, attacker receives tokens

Result:
  attacker bypasses the per-user allowlist and swaps on a curated pool.
  allowedSwapper[pool][attacker] was never set to true.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
