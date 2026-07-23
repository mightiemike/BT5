### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any Address to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument forwarded by the pool against the per-pool allowlist. The pool always passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (required for router-mediated swaps to work at all) simultaneously grants every public user the ability to bypass the allowlist.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` parameter forwarded to every configured extension: [2](#0-1) 

**Step 2 — The router calls the pool directly; the pool sees the router as `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` without forwarding the original caller: [3](#0-2) 

The router stores the original `msg.sender` only in transient storage for the payment callback — it is never forwarded to the pool or to any extension.

**Step 3 — The allowlist checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router: [4](#0-3) 

**Resulting bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only KYC'd addresses.
2. To let those users swap via the router, the admin must also call `setAllowedToSwap(pool, router, true)`.
3. Any non-allowlisted user calls `router.exactInputSingle(pool=X, ...)`.
4. The router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The non-allowlisted user successfully swaps on a restricted pool.

If the admin does **not** allowlist the router, the opposite failure occurs: even allowlisted users cannot swap through the router, forcing them to call the pool directly (breaking the standard UX).

---

### Impact Explanation

The swap allowlist is a production access-control extension intended to restrict which addresses may trade on a pool (e.g., for regulatory compliance, private pools, or whitelist-only launches). The bypass is complete: any public user can route through `MetricOmmSimpleRouter` and trade on a pool that was configured to deny them. LP assets in the pool are exposed to unrestricted trading, violating the pool admin's intended access policy and potentially causing direct financial harm (e.g., arbitrage by non-KYC'd actors against a restricted pool's liquidity).

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool that wants allowlisted users to use the router must allowlist the router address itself, which simultaneously opens the pool to all users. The misconfiguration is not avoidable within the current design — it is a structural consequence of the identity mismatch. No special preconditions, privileged access, or non-standard tokens are required.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two viable fixes:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that value. This requires a convention between the router and the extension.

2. **Add an explicit `swapper` parameter to the pool's `swap` signature**: The pool accepts a caller-supplied `swapper` address (validated against `msg.sender` or a trusted forwarder list) and passes it as `sender` to extensions. This is the cleanest fix but requires a breaking interface change.

Until fixed, pools that require per-user swap gating should not use `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that legitimate users can swap via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// attacker is NOT individually allowlisted:
// swapExtension.allowedSwapper[pool][attacker] == false

// Attacker routes through the public router:
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token1),
    tokenOut:        address(token0),
    zeroForOne:      false,
    amountIn:        1000,
    amountOutMinimum: 0,
    recipient:       attacker,
    deadline:        block.timestamp + 1,
    priceLimitX64:   type(uint128).max,
    extensionData:   ""
}));
// ✓ Swap succeeds — allowlist bypassed.
// The extension checked allowedSwapper[pool][router] == true,
// never checking allowedSwapper[pool][attacker] == false.
```

The pool's `_beforeSwap` receives `sender = address(router)` (the pool's `msg.sender`), not `attacker`. [5](#0-4)  The extension's check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`, which is `true`, so the guard passes for any caller of the router. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
