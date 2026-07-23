### Title
`SwapAllowlistExtension` gates on the router address instead of the actual user when swaps are routed through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`, breaking the per-user access-control invariant for every router-mediated swap.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
     → pool.swap(params.recipient, ...)          // msg.sender = router
     → _beforeSwap(msg.sender=router, recipient=user, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]        // ← wrong actor
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `sender` (the router) keyed against `msg.sender` (the pool): [3](#0-2) 

When the router calls the pool, `sender = address(router)`, so the check becomes `allowedSwapper[pool][router]`. The actual user's entry (`allowedSwapper[pool][user]`) is never consulted.

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the actual position beneficiary), not on `sender` (the adder/operator): [4](#0-3) 

The deposit extension handles the operator pattern correctly; the swap extension does not.

**The router never forwards the original caller's identity to the pool.** In `exactInputSingle`, the actual user (`msg.sender`) is stored only in the transient callback context for token-pull purposes; the pool's `swap()` call receives only `params.recipient` as the output address: [5](#0-4) 

---

### Impact Explanation

Two concrete failure modes arise:

1. **Allowlisted users cannot use the router.** If Alice is allowlisted (`allowedSwapper[pool][alice] = true`) but the router is not, Alice's router swap reverts because the extension sees `sender = router` and finds no entry. Alice is forced to call the pool directly, defeating the purpose of the periphery.

2. **Complete allowlist bypass when the router is allowlisted.** A pool admin who wants their allowlisted users to be able to use the router must add `allowedSwapper[pool][router] = true`. Because the router is a public, permissionless contract, this single entry opens the pool to **every** user: any non-allowlisted user can call `router.exactInputSingle(...)` and the extension will pass them through. The curated pool's access control is entirely nullified.

Impact: direct loss of the pool's curation guarantee; non-allowlisted users can trade on a pool that was explicitly restricted to a vetted set of counterparties.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural and expected action when the admin wants their vetted users to be able to use the standard periphery. The admin has no reason to suspect that allowlisting the router opens the pool to everyone, because the deposit allowlist (the analogous extension) does not have this flaw. The inconsistency between the two extensions makes the mistake easy to overlook.

---

### Recommendation

The `beforeSwap` hook receives both `sender` (the pool's `msg.sender`, i.e. the router) and `recipient` (the output address). Neither is reliably the "actual user" in all router flows. The correct fix is to pass the original caller's address through the extension payload (`extensionData`) and have the extension decode it, or to add a dedicated `swapper` field to the `beforeSwap` signature that the router populates explicitly. As a minimal mitigation, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used with direct pool calls.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
swapExt.setAllowedToSwap(address(pool), alice, true);

// Admin also allowlists the router so alice can use it:
swapExt.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) swaps through the router:
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ↑ Succeeds. Extension checked allowedSwapper[pool][router] = true.
// Bob bypassed the per-user allowlist entirely.
```

The pool calls `_beforeSwap(msg.sender=router, ...)`, the extension finds `allowedSwapper[pool][router] = true`, and Bob's swap executes despite never being allowlisted. [6](#0-5) [7](#0-6) [5](#0-4)

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
