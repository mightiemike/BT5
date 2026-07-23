### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Any User to Bypass the Swap Allowlist via the Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. Any user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              [msg.sender = router]
         → _beforeSwap(msg.sender=router, ...)
         → ExtensionCalling._callExtensionsInOrder(...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]   ← WRONG ACTOR
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called the pool — the router, not the original user: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` method) calls the pool, it passes no original-user context — the pool only sees the router as `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two concrete failure modes arise:

**Mode 1 — Full allowlist bypass (primary impact):** The pool admin must allowlist the router address to permit any router-mediated swap. Once `allowedSwapper[pool][router] = true`, every user on the network can swap through the router regardless of whether their own address is individually blocked. The allowlist is completely defeated for the router path.

**Mode 2 — Allowlisted users locked out:** If the admin does not allowlist the router (trying to enforce per-user control), then individually allowlisted users cannot use the router at all, even though they are supposed to be permitted. Core swap functionality is broken for the intended user set.

In both cases the `SwapAllowlistExtension` fails to enforce the policy the pool admin configured. Curated pools that rely on this extension to restrict trading to known counterparties are either fully open to all users or fully closed to router-mediated swaps.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap interface; most users will route through it rather than calling the pool directly.
- Any pool that deploys `SwapAllowlistExtension` and expects to support router-mediated swaps is immediately affected.
- No special privilege or setup is required: any user simply calls `exactInputSingle` on the router pointing at the curated pool.
- The `DepositAllowlistExtension` does **not** share this bug because it checks the `owner` parameter (which the liquidity adder passes correctly as the actual position owner), not the `sender`. [6](#0-5) 

---

### Recommendation

The `beforeSwap` hook signature already receives `sender` as the first argument. The fix requires the pool to pass the economically relevant actor — the original end-user — rather than its own `msg.sender`. One approach is to add an explicit `originalSender` field to the swap call (analogous to how `addLiquidity` separates `msg.sender` from `owner`). Alternatively, `SwapAllowlistExtension` can be documented as only safe for direct pool calls, and a separate mechanism (e.g., a trusted forwarder pattern in the router) must propagate the original caller identity.

A minimal interface change would add an `originalSender` parameter to `swap` and thread it through `_beforeSwap` and `IMetricOmmExtensions.beforeSwap`, so the extension can check the actual user rather than the intermediary.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin allowlists `alice` via `setAllowedToSwap(pool, alice, true)`.
3. Admin also allowlists the router via `setAllowedToSwap(pool, router, true)` (required for any router swap to work).
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true`.
7. `bob`'s swap succeeds despite never being allowlisted.

Conversely, if step 3 is omitted (router not allowlisted), `alice`'s router swap reverts at step 6 even though she is individually permitted. [7](#0-6) [8](#0-7)

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
