### Title
SwapAllowlistExtension Gates on Router Address Instead of Real User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. A pool admin who allowlists the router address (the natural step to enable standard periphery access) inadvertently opens the allowlist to every user on the internet, because the router's identity passes the check regardless of who called the router.

---

### Finding Description

**Root cause chain:**

1. `MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`. [1](#0-0) 

2. `ExtensionCalling._beforeSwap()` encodes that value and forwards it verbatim to every configured extension. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value from step 1 — the router address, not the end user. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` (and all other router entry points) calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool level. The original caller's address is stored only in transient callback state and is never forwarded to the extension. [4](#0-3) 

**The broken invariant:** The allowlist is supposed to gate the economically relevant actor — the user who initiates and pays for the swap. Instead it gates the intermediary contract (the router), which is a shared, permissionless entry point.

**Two concrete failure modes:**

| Scenario | Effect |
|---|---|
| Admin allowlists the router address to enable standard periphery access | Every user on the internet bypasses the allowlist |
| Admin allowlists individual users but not the router | Allowlisted users cannot use the router at all (broken UX) |

The first scenario is the fund-impacting one. A pool admin who wants to restrict swaps to KYC'd or institutional counterparties will naturally also want those users to be able to use the standard router. Allowlisting the router collapses the entire allowlist.

---

### Impact Explanation

**Direct loss / admin-boundary break.** A curated pool's swap allowlist is the primary access-control boundary. Bypassing it allows:

- Unauthorized users to trade on pools restricted to specific counterparties (regulatory / compliance breach).
- Any user to execute swaps on pools where the allowlist was the only guard preventing bad-price or high-volume extraction by untrusted actors.
- If the pool also carries an `OracleValueStopLossExtension`, the stop-loss still runs, but the allowlist bypass removes the first line of defense, widening the attack surface for watermark manipulation.

Severity: **High** — the allowlist is a core pool protection; its complete bypass by a supported periphery path is a direct admin-boundary break with fund-loss potential on curated pools.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the canonical, documented swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and wants users to access the pool through the standard router will allowlist the router address. This is the expected operational pattern, and it silently voids the allowlist for all users. No special knowledge or adversarial setup is required — any user who calls `exactInputSingle` through the router on a pool where the router is allowlisted bypasses the guard.

---

### Recommendation

The extension must check the **original user's identity**, not the intermediary's. Two viable approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender` for the allowlist:** If the pool's design intent is that the recipient of swap output is the gated party, `recipient` is already forwarded correctly. However, `recipient` can be set to any address, so this only works if the pool's curation model gates on who receives funds.

3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension level by reverting when `msg.sender` (the pool) is called from a known router address — though this is fragile.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` as a prefix in `extensionData`, and the extension decodes and checks that address when the pool is the caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow standard periphery access for allowlisted users)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) — msg.sender at pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
  - Attacker's swap executes on the curated pool despite never being allowlisted

Result:
  - SwapAllowlistExtension.beforeSwap() at line 37 evaluates
    allowedSwapper[pool][router] = true, not allowedSwapper[pool][attacker]
  - The allowlist is completely bypassed for any user who routes through the router
``` [5](#0-4) [4](#0-3) [6](#0-5)

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
