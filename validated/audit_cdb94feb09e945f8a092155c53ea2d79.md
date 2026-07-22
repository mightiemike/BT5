### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router is added to the allowlist (the only way to let legitimate users trade through it), every user of the router—including explicitly disallowed ones—can bypass the per-pool swap gate.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — Router is `msg.sender` of the pool's `swap` call.**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router the pool's `msg.sender`: [2](#0-1) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [3](#0-2) 

**Step 3 — Extension checks the router's allowlist entry, not the user's.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router address) and checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool: [4](#0-3) 

The allowlist is keyed `pool → swapper → bool`: [5](#0-4) 

**The dilemma is inescapable:**

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | All router users are blocked, even legitimate ones |
| Router **allowlisted** | All router users pass, including explicitly disallowed ones |

There is no middle path: the extension has no visibility into which EOA initiated the router call.

The `DepositAllowlistExtension` avoids this problem because it gates `owner` (the position owner explicitly supplied by the caller), not `sender` (the direct pool caller): [6](#0-5) 

The swap extension has no equivalent "real user" argument to fall back on.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a known set of addresses (e.g., KYC'd counterparties). Any disallowed user can bypass this gate by calling `MetricOmmSimpleRouter.exactInputSingle` with the curated pool as the target. The router is a public, permissionless contract. The disallowed user receives pool output tokens directly via the `recipient` parameter, suffering no friction beyond the normal swap fee. The pool's curation policy is completely nullified.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed alongside the core protocol. Any user who reads the periphery contracts or observes on-chain transactions can discover the bypass. No privileged access, special tokens, or flash-loan capital is required—a standard swap call suffices.

---

### Recommendation

Pass the originating user's address through `extensionData` from the router, and have `SwapAllowlistExtension.beforeSwap` decode and gate on that address when present. Alternatively, add a dedicated `originalSender` field to the extension hook signature so the pool can propagate the true initiator. A simpler short-term mitigation is to document that `SwapAllowlistExtension` is incompatible with router-mediated flows and must only be used with direct pool calls, but this breaks the intended periphery integration.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is allowed
  allowedSwapper[pool][bob]   = false  // bob is explicitly blocked

Attack (bob bypasses the gate):
  1. bob calls router.exactInputSingle({
       pool: curated_pool,
       tokenIn: token0,
       recipient: bob,
       ...
     })
  2. router calls pool.swap(bob, ...) — pool's msg.sender = router
  3. pool calls extension.beforeSwap(sender=router, ...)
  4. extension checks allowedSwapper[pool][router]
     → if router is allowlisted (required for alice to use the router),
       bob's call passes the gate
  5. bob receives token1 output; the allowlist policy is bypassed
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
