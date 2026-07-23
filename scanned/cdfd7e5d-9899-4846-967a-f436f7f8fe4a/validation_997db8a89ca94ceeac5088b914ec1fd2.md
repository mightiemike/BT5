### Title
`SwapAllowlistExtension` checks router address as swapper identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. If the pool admin allowlists the router to support router-mediated swaps for their curated users, every unprivileged user can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, ..., callbackData, extensionData)
              msg.sender = router
              → _beforeSwap(msg.sender, recipient, ...)
                            ^^^^^^^^^^
                            sender = router address
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks allowedSwapper[pool][router]
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router when the call came through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

**The invariant break:** The pool admin intends to gate individual swapper identities. To also support router-mediated swaps for their allowlisted users, the admin must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router — regardless of who the actual end-user is. Any unprivileged address can then bypass the allowlist by routing through `MetricOmmSimpleRouter`.

There is no mechanism for the pool to recover the original end-user's address; the router is the only `msg.sender` the pool ever sees.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only, or regulatory-restricted) with `SwapAllowlistExtension` and also allowlists the router loses all access control over who can swap. Any address — including those explicitly not allowlisted — can execute swaps against the pool's LP reserves by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. LP funds are exposed to unauthorized counterparties, breaking the pool's curation guarantee and potentially causing direct loss of LP principal if the pool carries favorable pricing for its intended participants.

---

### Likelihood Explanation

The bypass requires the pool admin to add the router to the allowlist. This is a natural and expected operational step: allowlisted users need the router for UX (slippage protection, multi-hop, WETH wrapping). The design gives the admin no alternative — there is no way to allow router-mediated swaps for specific users without also opening the gate to all users. Any production deployment of a curated pool that supports the router is therefore vulnerable.

---

### Recommendation

The `sender` value the pool passes to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should encode the original `msg.sender` into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when present.
2. **Extension-side (short-term):** Document that `SwapAllowlistExtension` is incompatible with router-mediated flows and must only be used on pools where all swappers call the pool directly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // KYC'd user
  allowedSwapper[pool][router] = true   // admin adds router to support alice's UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  pool.swap(bob, ...) is called with msg.sender = router
  _beforeSwap(router, bob, ...)
  SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → PASSES

  bob's swap executes against LP reserves despite not being allowlisted.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
