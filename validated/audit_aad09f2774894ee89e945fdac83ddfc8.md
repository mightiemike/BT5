### Title
`SwapAllowlistExtension` checks the router's address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the end-user. If the pool admin allowlists the router to support router-based swaps, every user — including those not on the allowlist — can bypass the guard by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router contract, so `sender` passed to the extension is the router address, not the end-user: [4](#0-3) 

The pool admin has two bad choices:

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Every user — including non-allowlisted ones — can swap by going through the router. Allowlist is fully bypassed. |
| **No** | Even allowlisted users cannot swap through the router. Router is permanently broken for this pool. |

Neither choice preserves the intended per-user access control.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` — the explicit position-owner parameter — rather than `sender`. Because `addLiquidity` takes `owner` as a named argument, the extension can identify the actual beneficiary regardless of who the caller is: [5](#0-4) 

`SwapAllowlistExtension` has no equivalent "actual swapper" parameter to inspect; the pool's `swap()` signature exposes only `recipient`, not the originating user.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses loses that restriction entirely once the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap in the restricted pool. The allowlist guard — the sole access-control mechanism for swaps — is rendered inoperative.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Any pool that deploys `SwapAllowlistExtension` and also wants to support router-based swaps must allowlist the router, which is the natural operational setup. The bypass is therefore reachable in every realistic production deployment of this extension.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the `recipient` parameter (the address that will receive swap output) or, preferably, the pool's `swap()` interface should be extended to carry an explicit `swapper` identity field analogous to `owner` in `addLiquidity`. As an immediate mitigation, the extension should document that it is incompatible with any intermediary router and that pools using it must require direct pool calls only.

Alternatively, mirror the `DepositAllowlistExtension` pattern: gate on the `recipient` address (second parameter of `beforeSwap`) rather than `sender`, since `recipient` is the address the user controls and specifies:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists the router so router-based swaps work.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT on the allowlist.
// Bob IS on the allowlist.
swapExtension.setAllowedToSwap(address(pool), bob, true);
// alice is NOT set.

// Alice calls the pool directly → correctly reverts.
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(alice, true, 1000, type(uint128).max, "", "");

// Alice calls through the router → succeeds, bypassing the allowlist.
// The extension sees sender = address(router), which IS allowlisted.
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: alice,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Alice successfully swaps despite not being on the allowlist.
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
