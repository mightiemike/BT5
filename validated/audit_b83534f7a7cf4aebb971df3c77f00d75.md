The complete call chain is traceable. Here is the analysis:

**Call chain for router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         └─ pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              └─ _beforeSwap(msg.sender=router, ...)
                   └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        └─ allowedSwapper[msg.sender][sender]
                           = allowedSwapper[pool][router]
```

**The structural identity confusion:**

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that `sender` to the extension: [2](#0-1) 

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user: [4](#0-3) 

**The consequence:**

The hook checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. This creates an irreconcilable dilemma for pool admins:

| Admin action | Result |
|---|---|
| Allowlist specific users only | Those users can swap directly but **not** through the router |
| Allowlist the router | **All** users can bypass the per-user allowlist via the router |

There is no configuration that allows specific users to swap through the router while blocking others. The allowlist cannot gate individual end users in router-mediated flows.

**Regarding the "two-transaction / timed-threshold" framing:**

The question's framing about timed-threshold manipulation, stale observations, and two-transaction sequences does not apply. The bypass is structural and single-transaction — it requires no timing, no state manipulation, and no precursor action. The `pauseLevel` check (`whenNotPaused`) blocks swaps entirely when paused, so the "paused for swaps but LP withdrawals live" scenario is irrelevant to this path.

**Verdict:**

---

### Title
Router-Mediated Swaps Check Router Address Instead of End User, Bypassing Per-User `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `sender` is the router contract, not the end user. If the router is allowlisted, any user can bypass per-user restrictions by routing through it.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it to the extension. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`. The original end user's address is never checked. A pool admin who intends to restrict swaps to a specific set of addresses cannot achieve this for router-mediated flows: either the router is not allowlisted (blocking all router users, including legitimate ones) or the router is allowlisted (allowing all users to bypass the per-user gate).

### Impact Explanation
The `SwapAllowlistExtension`'s core purpose — gating swaps by swapper identity — is defeated for any user who routes through `MetricOmmSimpleRouter`. Pools relying on this extension for access control (e.g., private pools, KYC-gated pools, protocol-only pools) can be accessed by any unprivileged user via the router if the router is allowlisted. This constitutes broken core pool functionality and an admin-boundary break.

### Likelihood Explanation
High. The router is a standard, publicly deployed periphery contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. No special setup, timing, or state manipulation is required.

### Recommendation
Pass the original end user's address through the call chain. One approach: include the originating user in `extensionData` and have the extension verify it against a router-signed attestation. Alternatively, the pool could accept an explicit `originator` parameter that the router populates with `msg.sender` before calling `pool.swap`, and the extension checks `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`. The router's trustworthiness must be established (e.g., factory-registered routers only) for this to be safe.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (required for any router swap to work).
3. Pool admin does NOT call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps in a pool they were not individually allowlisted for.

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
