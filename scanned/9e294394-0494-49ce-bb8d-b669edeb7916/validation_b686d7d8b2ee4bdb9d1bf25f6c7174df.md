### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User — Allowlist Bypass or Denial-of-Service for Router-Mediated Swaps - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual end user. The extension therefore checks the router's allowlist status, not the user's. This creates two failure modes: (1) if the router is allowlisted, every user bypasses the individual allowlist; (2) if the router is not allowlisted, allowlisted users cannot use the router at all.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Router calls `pool.swap` directly — its address becomes `msg.sender` in the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The router is `msg.sender` inside the pool for every single-hop and multi-hop path: [2](#0-1) 

For multi-hop `exactInput`, the same pattern holds for every hop: [3](#0-2) 

**Extension checks `sender` — which is the router, not the user:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument (the router): [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the economic actor), not `sender` (the direct caller): [5](#0-4) 

The asymmetry is the root cause: deposit allowlist gates the economically relevant actor (`owner`); swap allowlist gates the direct caller (`sender`), which is the router in all periphery-mediated flows.

---

### Impact Explanation

**Scenario A — Router is allowlisted (bypass):**
A pool admin who wants allowlisted users to be able to use the router adds the router address to the allowlist. This inadvertently opens the pool to *every* user, because any address can call `router.exactInputSingle(...)` and the extension will see `sender = router` (allowlisted). The individual per-user allowlist is completely defeated. Disallowed users can trade on a curated pool, draining LP assets or executing swaps the pool was designed to block.

**Scenario B — Router is not allowlisted (denial):**
Allowlisted users who attempt to swap through the router are rejected because the extension sees `sender = router` (not allowlisted). Legitimate users are locked out of the supported periphery path, breaking core swap functionality.

Both scenarios break the invariant: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."*

---

### Likelihood Explanation

The router is the primary user-facing swap entrypoint. Pool admins who configure a `SwapAllowlistExtension` and also want their users to use the router will naturally allowlist the router — triggering Scenario A. Scenario B is triggered by any allowlisted user who attempts a router swap without the admin having allowlisted the router. Both paths are reachable by normal, unprivileged users through the documented periphery.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **actual end user**, not the direct caller. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the address that economically benefits from the swap and is always set by the originating user, even through the router.
2. **Require the router to forward the originating user** — add a `swapperOverride` field to `extensionData` that the router populates with `msg.sender` (the actual user), and have the extension decode and check that address. The `onlyPool` guard on the extension already ensures only a legitimate pool can call it, so the pool's forwarding of router-supplied `extensionData` is the trust boundary.

The `DepositAllowlistExtension` pattern (checking `owner`, not `sender`) is the correct model to follow.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to enable router-based swaps for allowlisted users.
3. Non-allowlisted attacker calls:
     router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Pool calls _beforeSwap(msg.sender=router, ...)
5. Extension checks allowedSwapper[pool][router] → true → swap proceeds.
6. Attacker successfully swaps on a pool they were never individually allowlisted for.
```

The `SwapAllowlistExtension.beforeSwap` check at line 37 passes because `sender == router` (allowlisted), even though the actual user is not in the allowlist. [6](#0-5)

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
