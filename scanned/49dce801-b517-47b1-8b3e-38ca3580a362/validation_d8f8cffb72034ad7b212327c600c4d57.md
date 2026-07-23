### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the **actual user**. If the pool admin allowlists the router to support periphery-mediated swaps, every unpermissioned user can bypass the curated allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` value to every configured extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is the value the pool passed in — which is the **router's address** when the call originates from `MetricOmmSimpleRouter`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly: [3](#0-2) 

So the call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle
         → pool.swap(...)          [msg.sender = router]
             → _beforeSwap(msg.sender=router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]  ← checks router, not user
```

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an impossible choice:

- **Do not allowlist the router**: allowlisted users cannot use the public router at all; they must call the pool directly.
- **Allowlist the router**: the allowlist is completely bypassed — every user, including those explicitly blocked, can swap by routing through `MetricOmmSimpleRouter`.

In the second (operationally necessary) case, any unpermissioned user executes swaps on a pool that was designed to be restricted. This is a direct policy bypass with fund-impacting consequences: disallowed counterparties trade against LP liquidity on pools that were supposed to be curated.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user who knows the pool address can call `exactInputSingle` with no special privileges. The bypass requires only a single transaction through the standard router interface. Pool admins who want their allowlisted users to have a good UX will naturally allowlist the router, triggering the vulnerability.

---

### Recommendation

The pool must pass the **originating user** as `sender`, not `msg.sender`. Two standard approaches:

1. **Router forwards the real sender**: `MetricOmmSimpleRouter` passes `msg.sender` (the actual user) as a verified `sender` field in `extensionData`, and the extension reads it from there — but this requires the extension to trust the router, which reintroduces a trust assumption.

2. **Pool reads sender from transient storage**: The router writes the real payer into transient storage (it already does this for the callback context via `_setNextCallbackContext`). The pool or extension can read the real initiator from that slot instead of using `msg.sender`.

The cleanest fix is for `MetricOmmSimpleRouter` to write the real `msg.sender` into transient storage before calling `pool.swap`, and for `MetricOmmPool.swap` to pass that stored value — rather than its own `msg.sender` — as the `sender` argument to `_beforeSwap`. [5](#0-4) 

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists alice but not attacker.
// Admin also allowlists the router so alice can use it.

extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true); // required for alice to use router

// Attacker (not allowlisted) calls the router directly:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Succeeds: extension sees sender=router, router is allowlisted → no revert.
// Attacker swaps on a pool they were explicitly blocked from.
```

The extension checks `allowedSwapper[pool][router]` which is `true`, so the attacker's swap executes despite the attacker not being on the allowlist. [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```
