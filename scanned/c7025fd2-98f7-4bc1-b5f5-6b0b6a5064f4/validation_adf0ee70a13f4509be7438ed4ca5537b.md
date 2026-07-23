### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the user. If the pool admin allowlists the router (a natural action to enable router-based swaps), every user on-chain can bypass the swap allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's address is never inspected.

The same router-as-sender pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The existing integration test (`FullMetricExtensionTest`) never exercises the real router against the allowlist — it uses `TestCaller` contracts that call the pool directly, so the mismatch is invisible to the test suite: [6](#0-5) 

---

### Impact Explanation

**Bypass path (high impact):** A pool admin who wants to allow router-based swaps will allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user on-chain — including those explicitly excluded from the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle` and swap successfully. The allowlist is completely neutralised for all router-mediated swaps.

**Denial path (medium impact):** If the admin does not allowlist the router, then even explicitly allowlisted users cannot use the router; they must call `pool.swap()` directly. This breaks the expected UX and makes the router unusable for allowlisted pools.

Both outcomes break the core invariant of `SwapAllowlistExtension`: that only addresses explicitly permitted by the pool admin may swap.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented and deployed alongside the protocol. A pool admin who configures a swap allowlist and also wants users to be able to use the router will naturally allowlist the router address. There is no warning in the contract or documentation that doing so opens the pool to all users. The trigger requires only a standard admin configuration step followed by any unprivileged user calling the public router.

---

### Recommendation

The `sender` forwarded to extensions should represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **In the router:** pass the original `msg.sender` (the user) as the `recipient` or via `extensionData` so extensions can identify the real actor. Alternatively, have the router expose a dedicated entry point that the pool can query for the originating user.

2. **In `SwapAllowlistExtension`:** document clearly that `sender` is the direct caller of `pool.swap()`, and provide a companion extension that reads the originating user from a trusted router's transient storage (similar to how `MetricOmmSwapRouterBase` already stores the payer in transient slots).

3. **Short-term:** add an integration test that deploys the real `MetricOmmSimpleRouter`, configures a `SwapAllowlistExtension`, allowlists the router, and asserts that a non-allowlisted user is still blocked.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
MetricOmmPool pool = deployPoolWithBeforeSwapExtension(ext);
MetricOmmSimpleRouter router = new MetricOmmSimpleRouter(weth, factory);

// Admin allowlists the router so that "router users" can swap
ext.setAllowedToSwap(address(pool), address(router), true);

// Attacker: an address that is NOT on the allowlist
address attacker = makeAddr("attacker");
token0.mint(attacker, 1e18);
vm.prank(attacker);
token0.approve(address(router), type(uint256).max);

// Attacker routes through the router — pool sees msg.sender == router (allowlisted)
// Extension checks allowedSwapper[pool][router] == true → swap succeeds
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: attacker,
        deadline: block.timestamp + 1,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// ✓ swap succeeds — attacker bypassed the allowlist
```

The root cause is that `pool.swap()` passes `msg.sender` (the router) as `sender` to the extension hook, while the extension interprets `sender` as the identity to gate — but the router's address, not the user's address, is what gets checked. [3](#0-2) [1](#0-0) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
