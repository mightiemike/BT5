### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When `MetricOmmSimpleRouter` is the caller of `pool.swap()`, `msg.sender` inside the pool is the **router address**, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any user who is not on the allowlist can bypass the gate by routing through the public router.

---

### Finding Description

**Call chain:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender, ...)  // sender = router
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  // ← wrong actor checked
```

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The original user who called the router is never surfaced to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

**Consequence:** A pool admin who wants allowlisted users to be able to swap via the router must add the router to the allowlist. The moment the router is allowlisted, every user on the network can bypass the allowlist by routing through it. Conversely, if the router is not allowlisted, even legitimately allowlisted users cannot use the router — breaking the intended UX.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly passed by the caller), not `sender` (the pool's `msg.sender`): [5](#0-4) 

---

### Impact Explanation

Any user can trade on a curated pool that is supposed to be restricted to an allowlist. This breaks the core access-control invariant of the `SwapAllowlistExtension` and allows unauthorized parties to execute swaps against LP positions, potentially extracting value from LPs who deposited under the assumption that only vetted counterparties could trade against them.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol. Any pool that configures `SwapAllowlistExtension` and also needs to support router-based swaps is immediately vulnerable. The bypass requires no special privileges — any EOA can call the router.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **original initiator** of the swap, not on the intermediate caller. Two options:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` or add an `originator` field to the hook signature:** The cleanest fix is to have the pool (or the router) surface the true economic actor in a tamper-evident way that the extension can verify.

The simplest production fix is to have the extension check `sender` only when `sender` is not a known router, or to require the router to forward the original caller in `extensionData` and have the extension decode it when present.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists only `trustedUser`.
// Admin also allowlists the router so trustedUser can use it.
swapExt.setAllowedToSwap(pool, address(router), true);   // ← required for router to work
swapExt.setAllowedToSwap(pool, trustedUser, true);

// bannedUser is NOT on the allowlist.
// bannedUser calls the router directly:
vm.prank(bannedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    ...
    extensionData: ""
}));
// ↑ Succeeds because the extension sees sender=router, which IS allowlisted.
// bannedUser has bypassed the swap allowlist entirely.
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
