### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's `msg.sender` — the immediate caller of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router (a natural action to enable router-mediated trading) inadvertently opens the pool to every user of the router, defeating the per-user access control the extension is designed to enforce.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks: allowedSwapper[pool][router]  ← router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this as the first argument to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = router address
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key), and `sender` is the router. The check becomes `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for **every user** who calls through the router, regardless of whether that user is individually allowlisted.

**Contrast with `DepositAllowlistExtension`**, which correctly gates the position `owner` (the economically relevant party), not the immediate caller:

```solidity
// DepositAllowlistExtension.sol line 38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit allowlist checks `owner` (passed explicitly and preserved through the liquidity adder), while the swap allowlist checks `sender` (the immediate pool caller, which is the router). This asymmetry means the deposit guard is correctly bound to the end user but the swap guard is not.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to specific counterparties (e.g., KYC'd addresses, whitelisted market makers, or regulated participants). To allow those counterparties to use the official `MetricOmmSimpleRouter`, the admin must allowlist the router. Once the router is allowlisted, **any user** — including those explicitly not on the allowlist — can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. The pool's LP funds are then exposed to swaps from unauthorized parties, defeating the access control the extension was configured to enforce. This is an admin-boundary break: an unprivileged path (the public router) bypasses a pool-admin-configured guard.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is a foreseeable and natural action: a pool that restricts swappers to a curated set still needs those swappers to be able to use the standard periphery router. The admin has no other way to enable router-mediated swaps for allowlisted users without also opening the pool to all router users. The bypass is therefore reachable in any production deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

---

### Recommendation

The extension should gate the end user, not the immediate pool caller. Two approaches:

1. **Pass the end user through the router.** Add a `swapper` field to `extensionData` that the router populates with `msg.sender`, and have `SwapAllowlistExtension` decode and check that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to forward the real user.** The router could be modified to pass the real user as the `recipient` or via `extensionData`, and the extension updated to read from there.

3. **Mirror the deposit allowlist pattern.** The deposit allowlist correctly checks `owner` (the position owner), which is preserved through the liquidity adder. The swap allowlist should similarly check a user-supplied identity that the router preserves, rather than the immediate `msg.sender` of the pool call.

---

### Proof of Concept

```solidity
// Setup:
// - Pool configured with SwapAllowlistExtension
// - Pool admin allowlists the router: swapAllowlist.setAllowedToSwap(pool, address(router), true)
// - Alice (address not individually allowlisted) wants to swap

// Alice calls through the router:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: alice,
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// router calls pool.swap(...) with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// Alice's swap succeeds despite not being individually allowlisted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
