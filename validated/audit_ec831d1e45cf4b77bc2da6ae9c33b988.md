### Title
`SwapAllowlistExtension` Allowlist Guard Bypassed via Router Intermediary — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is configured to gate swaps by swapper address per pool. However, the extension checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call — the router contract — not the actual end user. When the pool admin allowlists the router to enable standard router-based swaps, every unprivileged user can bypass the allowlist restriction entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router address**, so `sender` forwarded to the extension is the router, not the end user. The extension evaluates `allowedSwapper[pool][router]` — a single entry that, if set to `true`, opens the gate for every user who routes through that contract.

The `DepositAllowlistExtension` avoids this problem by checking the `owner` parameter (the explicit position owner) rather than `sender`: [5](#0-4) 

No equivalent correction exists in `SwapAllowlistExtension`.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., only whitelisted market makers may swap) and allowlists the router — the natural step to enable standard UX — inadvertently opens the pool to all callers. Any unprivileged address can call `router.exactInputSingle()` and execute swaps that the allowlist was meant to block. Unauthorized swappers can extract value from LP positions through oracle-priced trades the LPs never consented to serve, constituting a direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point. Any pool admin who wants users to interact normally must allowlist it. The bypass is therefore triggered by a routine, expected administrative action, not an exotic configuration. No special privilege beyond being an ordinary user is required to exploit it once the router is allowlisted.

---

### Recommendation

`SwapAllowlistExtension` should gate on the actual end user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Mirror `DepositAllowlistExtension`'s pattern**: add a `recipient` or `originator` field to the swap hook signature that the pool populates from a trusted source, and check that field instead of `sender`.

Additionally, document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`, so pool admins are not surprised.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, address(router), true);
   (necessary for any user to swap via the standard router)
3. Unauthorized user (not individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...});
4. Router calls pool.swap(...) — msg.sender inside pool = router.
5. Pool calls _beforeSwap(router, ...).
6. Extension evaluates:
       allowedSwapper[pool][router] == true  →  no revert
7. Swap executes. Unauthorized user receives output tokens.
   LP funds are consumed by a swap the allowlist was meant to block.
```

The unit tests in `SwapAllowlistSubExtension.t.sol` only exercise the extension in isolation with `vm.prank(address(pool))` and a literal swapper address — they never route through the router, so the bypass is untested and undetected. [6](#0-5)

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
