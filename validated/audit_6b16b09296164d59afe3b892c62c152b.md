### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call, so the extension sees the router address as the swapper — not the actual end user. If the pool admin allowlists the router to enable router-mediated swaps on a curated pool, every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         └─ pool.swap(params.recipient, ..., params.extensionData)   [msg.sender = router]
              └─ _beforeSwap(msg.sender=router, recipient, ...)
                   └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        └─ allowedSwapper[pool][router]  ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address when the call came through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender` (the end user) as the `sender` argument: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with `msg.sender = router`.

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist correctly gates on `owner` (the second argument), which is the position owner regardless of who the caller is. The swap allowlist has no equivalent "owner" concept and instead gates on `sender`, which collapses to the router address for all router-mediated swaps. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (to support the official periphery) inadvertently opens the allowlist to every user on the network. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because `allowedSwapper[pool][router] == true`. The per-user curation is completely defeated. This constitutes a broken core pool functionality (allowlist bypass) with direct fund-impact consequences: the pool receives swaps from actors the admin explicitly intended to exclude, which can drain LP value or violate regulatory/compliance constraints the allowlist was meant to enforce.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is a production periphery contract. Allowlisting the router is the natural and expected configuration for any curated pool that also wants to support the official router UX. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to a public router function. Any user who discovers the allowlist can trivially route around it.

---

### Recommendation

The extension must gate on the actual end user, not the intermediary. Two approaches:

1. **Pass the original user through the router:** Add a `swapFor(address user, ...)` pattern or use transient storage to record the originating user before calling `pool.swap`, then expose it via a callback or a read function the extension can query.

2. **Gate on `recipient` instead of `sender` for router flows:** This is imperfect because `recipient` can be a third party.

3. **Preferred — mirror the deposit pattern:** Introduce a `swapOwner` concept analogous to `owner` in `addLiquidity`, so the pool carries the economically relevant actor through the hook regardless of the intermediary. The router would set this to `msg.sender` before calling the pool.

Until fixed, pool admins must **not** allowlist the router address on curated pools; they must require users to call `pool.swap` directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist alice (alice is a non-permitted user)

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  - alice's swap executes despite not being on the allowlist

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

The existing unit tests in `SwapAllowlistSubExtension.t.sol` and `FullMetricExtension.t.sol` only test direct pool calls or calls through a `TestCaller` wrapper — they never exercise the `MetricOmmSimpleRouter` path against a pool with `SwapAllowlistExtension` active, so this bypass is untested. [6](#0-5)

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
