### Title
`SwapAllowlistExtension` checks the router's address as `sender`, not the actual user — any user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), the allowlist is completely bypassed for every user who routes through it.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
     → IMetricOmmPoolActions(params.pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool.swap: _beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]  ← NOT the actual user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whatever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls the pool directly, so `sender` seen by the extension is always the router address, never the end user. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or institutional participants). The allowlist is the only on-chain enforcement mechanism for this restriction.

**Bypass path:** The pool admin must allowlist the router address (`allowedSwapper[pool][router] = true`) for any allowlisted user to use the standard periphery. Once the router is allowlisted, **every user** — including those explicitly not allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension check passes because `sender = router` is allowlisted. The actual caller's identity is never verified.

**Consequence:** LP funds in a pool intended to be permissioned are exposed to unrestricted counterparties. The pool admin cannot simultaneously allow legitimate users to use the router and block unauthorized users — the two goals are mutually exclusive under the current design. This breaks the core allowlist invariant and constitutes a broken access-control guard with direct fund-exposure impact.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract. Any user can call it.
- The bypass requires no special privileges, no malicious setup, and no non-standard tokens.
- The only precondition is that the pool admin has allowlisted the router (a necessary step for legitimate users to use the router), which is the expected operational configuration.
- Likelihood is **high** whenever a pool is configured with `SwapAllowlistExtension` and the router is allowlisted.

---

### Recommendation

Pass the **original initiating user** through the call chain rather than the immediate `msg.sender`. Two concrete approaches:

1. **Router-side:** Have the router encode the real user address in `extensionData` and have the extension decode it — but this requires trusting the router, which is fragile.

2. **Preferred — pool-side:** Add a `recipient` or `originator` field that the pool populates from a verified source (e.g., a transient-storage slot set by the router before calling the pool, similar to how the liquidity adder stores the payer in `T_SLOT_PAY_PAYER`). The extension then reads the verified originator rather than the raw `sender`.

3. **Simplest fix:** Document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this makes the router unusable for permissioned pools and is not a code-level fix.

---

### Proof of Concept

```solidity
// Setup:
// 1. Pool deployed with SwapAllowlistExtension
// 2. Admin allowlists alice: allowedSwapper[pool][alice] = true
// 3. Admin allowlists router so alice can use it: allowedSwapper[pool][router] = true

// Attack: Bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob); // bob is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ Swap succeeds — SwapAllowlistExtension checked allowedSwapper[pool][router] = true
// ✓ Bob traded against the pool's LP funds without being allowlisted
```

The extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`, which is `true`. Bob's address is never consulted. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
