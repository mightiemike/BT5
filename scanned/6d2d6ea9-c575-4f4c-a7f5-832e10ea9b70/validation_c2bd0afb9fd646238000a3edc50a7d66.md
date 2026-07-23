### Title
`SwapAllowlistExtension` gates the router address instead of the economic actor, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the user's address. If the pool admin allowlists the router (the natural action to enable router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-pool swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = router`, and the check becomes `allowedSwapper[pool][router]`. The pool admin has two mutually exclusive options:

| Admin action | Result |
|---|---|
| Allowlist the router | **Every user** can swap through the router — individual allowlist is nullified |
| Allowlist individual users only | Allowlisted users **cannot** use the router; their swap flow is broken |

There is no configuration that simultaneously allows router-mediated swaps and restricts them to specific users.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the position owner), which is preserved end-to-end through the liquidity adder: [5](#0-4) 

The swap extension has no equivalent identity-preserving mechanism.

---

### Impact Explanation

Any unprivileged user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. The allowlist is an admin-configured access-control boundary; its bypass is an admin-boundary break. Pools deployed with `SwapAllowlistExtension` for regulatory compliance, KYC gating, or LP-protection purposes silently lose that protection the moment the router is allowlisted. Allowlisted users who rely on the router for slippage protection or multi-hop routing find their swap flow broken if the router is not allowlisted.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural, expected action for any pool that intends to support the standard periphery. No adversarial setup is needed; the bypass is reachable by any user who calls the public router after the admin performs this routine configuration step.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require a trusted forwarder pattern for router calls**: The extension verifies that if `sender` is the router, the router has forwarded a signed or encoded user identity that is individually allowlisted.

A minimal guard that at least prevents silent bypass:

```solidity
// In SwapAllowlistExtension.beforeSwap:
// Reject the router as a blanket pass-through; require individual user identity.
if (isRouter[sender]) revert RouterNotAllowedAsSwapper();
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured in beforeSwap slot.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
3. Pool admin calls setAllowedToSwap(pool, alice, true)    // intend to restrict to alice only
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls extension.beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → Bob's swap succeeds — individual allowlist bypassed.

5. Alice calls router.exactInputSingle({pool: pool, ...})
   → same path, also succeeds (correct, but indistinguishable from Bob).

6. Pool admin removes router from allowlist to stop Bob.
   → Alice's router-mediated swaps now revert (NotAllowedToSwap).
   → Alice must call pool.swap() directly, losing router slippage protection.
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
