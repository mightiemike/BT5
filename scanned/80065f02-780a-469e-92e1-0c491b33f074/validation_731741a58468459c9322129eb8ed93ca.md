### Title
SwapAllowlistExtension Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed on an allowlisted pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every before-swap hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(recipient, ...)` directly, so the pool sees `msg.sender = router`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router never forwards the originating user's address to the pool, and the extension ignores the `extensionData` bytes entirely — there is no in-band channel for the real caller identity.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, regardless of whether that caller is individually permitted.

The same structural problem exists for the multi-hop `exactInput` path: [4](#0-3) 

---

### Impact Explanation

The `SwapAllowlistExtension` is a production curation guard. Its stated purpose is to gate `swap` by swapper address, per pool. When the router is allowlisted, the guard fails open for every public user of the router. Non-allowlisted users can trade on pools that the pool admin intended to restrict, constituting a direct admin-boundary break and curation failure. The RESEARCHER.md explicitly classifies this as a High-impact vector: *"High direct loss or curation failure if disallowed users can still trade or deposit."* [5](#0-4) 

---

### Likelihood Explanation

The bypass requires only that the pool admin allowlists the router — a necessary step for any allowlisted pool to be usable via the supported periphery. The admin has no way to allowlist the router for specific users only; the allowlist entry is binary (`allowedSwapper[pool][router] = true`). Any user who calls `exactInputSingle` or `exactInput` on the router against such a pool bypasses the guard with zero additional preconditions.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the intermediary. Two complementary fixes:

1. **Router-side**: Have the router encode the originating user's address into `extensionData` (or a dedicated field) so extensions can recover it.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode and verify the original caller from `extensionData` when `sender` is a known router, or the protocol should document that allowlisted pools must not allowlist the router and must require direct pool interaction only.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the LP position owner passed explicitly by the caller), not the pool's `msg.sender`: [6](#0-5) 

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → **passes**.
8. Bob's swap executes on the restricted pool, bypassing the per-user allowlist entirely. [7](#0-6)

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

**File:** RESEARCHER.md (L56-58)
```markdown

## High-Value Scenarios To Always Test

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
