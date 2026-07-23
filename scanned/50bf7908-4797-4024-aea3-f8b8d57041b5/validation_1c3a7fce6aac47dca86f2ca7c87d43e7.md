### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any unprivileged caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (a natural assumption for a "trusted" periphery), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool sees `msg.sender = router`: [4](#0-3) 

So the allowlist check becomes: *"Is the router address allowed to swap on this pool?"* — not *"Is the actual user allowed?"*

The actual user's identity is stored in transient storage as the payer (`_getPayer()`) and is never surfaced to the extension. The extension has no way to recover the real initiator.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position recipient), which is passed explicitly and is independent of who calls the pool: [5](#0-4) 

The swap extension lacks an equivalent economically-correct identity to check.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-only, institutional-only) and allowlists the router as a trusted periphery contract inadvertently opens the pool to **all users**. Any non-allowlisted address can call `router.exactInputSingle(pool, ...)` and execute swaps at oracle-derived prices against LP capital that was never intended to be accessible to them. This constitutes a direct bypass of the pool's access-control invariant and can result in LP funds being traded against at unfavorable oracle prices by actors the pool admin explicitly excluded.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery swap entry point. Pool admins who want allowlisted users to be able to use the standard router UI must allowlist the router address — otherwise their own approved users cannot swap through the router either. This creates a forced choice: either allowlist the router (opening the pool to everyone) or block the router (breaking the standard UX for approved users). The bypass is therefore reachable through normal, expected usage of the supported periphery.

---

### Recommendation

The `beforeSwap` hook should gate on the **economically relevant actor** — the address that initiated the router call and will pay for the swap — rather than the intermediate caller. Two approaches:

1. **Pass the original initiator explicitly**: Extend the `beforeSwap` signature (or `extensionData`) to carry the original `msg.sender` from the router, and have the extension check that value. The router already tracks the payer in transient storage; it could encode it into `extensionData`.

2. **Mirror the deposit pattern**: Like `DepositAllowlistExtension` checks `owner` (the position recipient), the swap extension could check `recipient` as the gated identity, since the recipient is the economic beneficiary of the swap output. This is not a perfect substitute for the payer but is at least router-independent.

Until fixed, pool admins should be warned **not** to allowlist the router address, and the router should not be allowlisted by default.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use the UI
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...)
   → pool calls extension.beforeSwap(router, bob, ...)
   → check: allowedSwapper[pool][router] == true  ✓  (passes!)
5. Bob's swap executes against LP capital at oracle prices.
   Alice's LP position is traded against by an actor the admin explicitly excluded.
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
