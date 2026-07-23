### Title
`SwapAllowlistExtension` checks the router's address instead of the originating user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted rather than the originating user. If the pool admin allowlists the router (the natural action to let allowlisted users use the router), every unpermissioned user can bypass the swap gate by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)` with itself as `msg.sender`: [4](#0-3) 

The pool therefore passes `sender = router_address` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; must call the pool directly |
| **Allowlist the router** | Every user — including non-allowlisted ones — can bypass the gate by routing through the public router |

The router validates that the pool is registered on the factory but does not restrict who may call it: [5](#0-4) 

So any unpermissioned address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on a restricted pool as long as the router is allowlisted.

---

### Impact Explanation

**High.** The swap allowlist is the primary mechanism for restricting who may trade against a pool's LP funds. Bypassing it lets unauthorized users execute swaps at oracle-derived bid/ask prices, draining token0 or token1 from the pool's bins. LP principal is directly at risk because the pool transfers tokens to the swap recipient before the callback settles the input side: [6](#0-5) 

Every unauthorized swap reduces bin balances and shifts the pool cursor, corrupting the share-price accounting that all subsequent LP withdrawals depend on. This matches the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact gates.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router — a natural and expected operational step for any pool that wants its allowlisted users to benefit from multi-hop routing, slippage protection, and deadline enforcement. Once the router is allowlisted (even for a single legitimate user), the gate is open to the entire public. No privileged access, no special token, and no malicious setup is required beyond calling the public router.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** The router already tracks the payer in transient storage (`_getPayer()`). Encode the original `msg.sender` into `extensionData` before calling the pool, and have the extension decode and check that value instead of the raw `sender` argument.

2. **Alternatively, check `sender` in the extension only when `sender` is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to attest the real user identity via `extensionData`.

The simplest safe default: document that pools using `SwapAllowlistExtension` must **not** allowlist any public router, and instead require allowlisted users to call the pool directly. This is a usability trade-off but closes the bypass without a code change.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)      // admin allowlists router so alice can use it
4. Add liquidity to the pool (e.g., 100 000 token0 in bin +4).

Attack
──────
5. Bob (address NOT in allowedSwapper) calls:
       router.exactInputSingle({
           pool:      pool,
           tokenIn:   token1,
           zeroForOne: false,
           amountIn:  5000,
           recipient: bob,
           ...
       })

6. Router calls pool.swap(bob, false, 5000, ...) with msg.sender = router.
7. Pool calls _beforeSwap(sender=router, ...).
8. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
9. Bob receives token0 from the pool without being allowlisted.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; Bob drains token0 from LP bins
```

The root cause is at `SwapAllowlistExtension.sol:37` where `sender` is the router address, not the originating user, whenever the swap is routed through `MetricOmmSimpleRouter`. [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L250-278)
```text
    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```
