### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router address (the natural step to enable router-mediated swaps on a curated pool), every unprivileged user can bypass the per-user allowlist by calling any of the router's public `exact*` entry points.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool — the router when the user goes through periphery: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender` (the real user) into the pool: [4](#0-3) 

The allowlist is keyed `allowedSwapper[pool][router]`. Because `MetricOmmSimpleRouter` is a public, permissionless contract, allowlisting it is equivalent to setting `allowAllSwappers[pool] = true`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (the expected step to support the standard periphery entry point) inadvertently opens the pool to every user on-chain. Any non-allowlisted address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the public router and execute swaps against LP funds that the allowlist was meant to protect. The LP principal is directly exposed to unauthorized traders, constituting a High-severity allowlist bypass with direct loss of curation policy and potential LP value leakage.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, production-grade user entry point. Pool admins who want to support normal user flows will allowlist it. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single public router call suffices. The existing test suite (`FullMetricExtension.t.sol`) only exercises the `TestCaller`-direct-pool path and never tests the router path against an allowlisted pool, so the gap is undetected by current tests. [5](#0-4) 

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the router.** Add an `originSender` field to `extensionData` that the router encodes before calling the pool, and have the extension decode and check it. This requires a convention between the router and the extension.

2. **Use `tx.origin` as a fallback identity.** When `sender` is a known router, fall back to `tx.origin`. This is safe here because the extension is only checking authorization, not paying funds.

3. **Gate by `recipient` instead of `sender`.** For swap allowlists, the recipient (who receives output tokens) is often the economically relevant actor and is passed through the router unchanged.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes it when the immediate caller is a trusted router.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so legitimate users can swap via periphery
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) bypasses the allowlist:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        recipient:       attacker,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
// Succeeds: extension sees sender=router, which is allowlisted.
// Attacker swaps against LP funds without being individually authorized.
```

The pool's `_beforeSwap` receives `sender = address(router)`. The extension checks `allowedSwapper[pool][router] == true` and passes. The actual attacker address is never inspected. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
