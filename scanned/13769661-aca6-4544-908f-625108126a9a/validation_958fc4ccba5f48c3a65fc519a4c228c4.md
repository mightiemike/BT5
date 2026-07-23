Let me look at the key files more carefully to confirm the exact vulnerability.

### Title
`SwapAllowlistExtension` gates the router address instead of the end-user identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks whether the **router** is allowlisted rather than the actual end user. If the pool admin allowlists the router to enable router-mediated swaps for curated-pool users, every unprivileged address—including those never individually allowlisted—can bypass the per-user curation policy by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender /*= router*/, ...)
                   → ExtensionCalling._callExtensionsInOrder(...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Contrast with `DepositAllowlistExtension`:** that extension correctly ignores `sender` and checks `owner` (the position owner), which is the economically relevant actor for deposits. `SwapAllowlistExtension` has no equivalent correction for the swap path.

**Test gap:** `FullMetricExtension.t.sol` only exercises `TestCaller` contracts that call the pool directly. No test routes through `MetricOmmSimpleRouter`, so the bypass is never exercised.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (the natural step to let allowlisted users trade via the standard UX) inadvertently opens the gate to every address. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool; the extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and permits the swap. The curation policy is completely nullified. Unauthorized swaps drain LP-owned inventory at oracle prices, constituting a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is a natural and expected operational step: without it, individually allowlisted users cannot use the standard periphery UX and must call the pool directly. The admin has no in-protocol signal that allowlisting the router collapses per-user gating to all-or-nothing. The router is a public, permissionless contract, so once the router is allowlisted, any address can exploit the bypass without any further privilege.

---

### Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, do not rely solely on the `sender` argument for identity. Either:
- Require that the pool's `swap` is always called directly (document that router-mediated swaps are incompatible with this extension), or
- Have the extension decode the original end-user address from `extensionData` and verify it against the allowlist, with the router being responsible for injecting `msg.sender` into `extensionData` before forwarding.

**Long term:** Add an integration test that routes through `MetricOmmSimpleRouter` against a pool protected by `SwapAllowlistExtension` and asserts that a non-allowlisted end user is rejected even when the router itself is allowlisted.

---

### Proof of Concept

```solidity
// 1. Pool admin deploys pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// pool deployed with ext as beforeSwap hook

// 2. Admin allowlists Alice (a KYC'd user) directly
ext.setAllowedToSwap(address(pool), alice, true);

// 3. Admin allowlists the router so Alice can use the standard UX
ext.setAllowedToSwap(address(pool), address(router), true);

// 4. Bob (never allowlisted) calls the router — succeeds
// router calls pool.swap(...) with msg.sender = router
// extension checks allowedSwapper[pool][router] == true → passes
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Bob's swap executes despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
