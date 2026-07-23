The call chain is fully traceable. Here is the exact path:

**Step 1:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` — the router is `msg.sender` to the pool. [1](#0-0) 

**Step 2:** `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passing the **router address** as `sender`. [2](#0-1) 

**Step 3:** `ExtensionCalling._beforeSwap` forwards that `sender` (= router) to the extension's `beforeSwap`. [3](#0-2) 

**Step 4:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes regardless of who the original caller was. [4](#0-3) 

The bypass is real and requires no privileged action beyond the pool admin having allowlisted the router — a completely normal and expected configuration. The vulnerability is confirmed.

---

### Title
Router-Mediated Swap Bypasses Per-User `SwapAllowlistExtension` Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` passed to the extension is the **router address**, not the original user. Any user can bypass per-user swap gating by routing through an allowlisted router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`.

If a pool admin allowlists the router (a natural and expected configuration to permit normal trading), every user who routes through `MetricOmmSimpleRouter` passes the allowlist check unconditionally, regardless of whether they are individually allowlisted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with the router as `msg.sender`.

### Impact Explanation
The `SwapAllowlistExtension` is the sole mechanism for per-user swap access control. Its invariant — that only explicitly allowlisted addresses can swap — is completely broken for router-mediated swaps when the router is allowlisted. Any unprivileged user can swap on a pool that was intended to be restricted to a specific set of addresses.

This constitutes broken core pool functionality: the access-control extension does not gate the actor it was designed to gate.

### Likelihood Explanation
The router is a public, permissionless contract. Any user can call it. A pool admin who wants to allow normal trading through the official router will naturally add the router to the allowlist. Once the router is allowlisted, the per-user gate is permanently open to all router users. No special knowledge or privileged access is required beyond knowing the router address.

### Recommendation
The `SwapAllowlistExtension` should check the **original initiator**, not the immediate caller of `pool.swap`. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it (requires trust in the router, which is acceptable for a known factory-deployed router).
2. **Check `recipient` instead of `sender`**: If the pool's design intent is that the recipient is the economically relevant party, gate on `recipient`. However, this changes semantics.
3. **Document that `sender` is the immediate caller**: If the intended model is to allowlist routers (not end users), the extension name and admin tooling should reflect this, and per-user gating should be implemented differently.

### Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension
// allowedSwapper[pool][router] = true   (admin allowlists the router)
// allowedSwapper[pool][attacker] = false (attacker is NOT allowlisted)

// Attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    ...
}));
// pool.swap is called with msg.sender = router
// _beforeSwap(router, ...) is called
// extension checks allowedSwapper[pool][router] == true → PASSES
// Swap executes despite attacker not being in the allowlist
```

### Citations

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
