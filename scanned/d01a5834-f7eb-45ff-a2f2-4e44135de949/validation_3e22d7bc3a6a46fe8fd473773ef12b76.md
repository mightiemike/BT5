### Title
SwapAllowlistExtension gates the router address instead of the original user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router address to enable router-mediated swaps, every user—including those not on the allowlist—can bypass the per-user gate by calling any `exact*` function on the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist keyed by `msg.sender` (the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` as `msg.sender`: [4](#0-3) 

The pool therefore passes the **router's address** as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The original user's identity is stored only in the router's transient callback context for payment settlement and is never forwarded to the pool or the extension.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot swap via the router at all.
- **Allowlist the router** → every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through the public router.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's liquidity, receiving output tokens at oracle-derived prices. LP funds are directly at risk because the allowlist was the only mechanism preventing those users from trading against the pool.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the periphery. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the bypass to all users. The router is a deployed, public, permissionless contract, so no special access or setup is required for an attacker.

### Recommendation

The pool's `swap()` function should accept an explicit `swapper` parameter (the economically relevant actor) separate from `msg.sender` (the settlement payer), and pass that value as `sender` to extensions. Alternatively, `SwapAllowlistExtension.beforeSwap` should check the `recipient` or require the router to forward the original caller's address through `extensionData`, and the extension should decode and verify it. The simplest safe fix is to add a `swapper` field to the pool's `swap` signature and have the router populate it with `msg.sender` before calling the pool.

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so that allowlisted users can trade via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT on the per-user allowlist.
// Direct swap reverts:
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(alice, true, 1000, 0, "", "");

// But Alice bypasses the allowlist via the router:
vm.prank(alice);
// Succeeds — extension sees sender == address(router), which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Alice receives token1 output — allowlist bypassed.
``` [5](#0-4) [6](#0-5) [4](#0-3)

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
