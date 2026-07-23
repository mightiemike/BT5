Now let me look at the router's swap implementation and how it calls the pool, to trace the exact `sender` identity passed to the extension.

I have enough information to produce the full analysis. Let me verify the exact call chain one more time.

### Title
Swap Allowlist Bypass via Router: `SwapAllowlistExtension` gates the router address instead of the actual end user — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual end user. If the pool admin allowlists the router address (the only way to let allowlisted users swap via the router), every non-allowlisted user can bypass the swap gate by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the extension call:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The identity mismatch:** For allowlisted users to swap through the router, the pool admin must add the router's address to `allowedSwapper[pool]`. Once the router is allowlisted, the extension sees `sender = router` for every router-mediated call regardless of who the actual end user is. Any non-allowlisted user can call `exactInputSingle` (or any other router entry point) and the extension will approve the swap because it only sees the router's allowlisted address.

The existing test suite only exercises direct pool calls via a `TestCaller` contract; no test covers a router-mediated swap against an allowlisted pool, leaving this gap undetected. [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely for router-mediated swaps once the router is allowlisted. Non-allowlisted users can execute arbitrary swaps against the pool's liquidity, exposing LP providers to trades they explicitly intended to block. This constitutes a broken core pool access-control invariant with direct LP-loss potential.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router address. This is the natural and necessary step for any allowlisted user who wants to swap through the standard periphery router rather than calling the pool directly. A pool admin who sets up a swap allowlist and also wants to support the router will inevitably create this condition. The router is a public, permissionless contract, so once the router is allowlisted, the bypass is available to any address on-chain with no further preconditions.

---

### Recommendation

1. **Pass the real end-user identity through `extensionData`**: The router should encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension can then decode and verify that address against the allowlist. This requires a convention between the router and the extension.

2. **Alternatively, expose a dedicated `swapFor` entry point on the pool** that accepts an explicit `swapper` address and enforces `msg.sender == swapper || isApprovedOperator(swapper, msg.sender)`, mirroring the `addLiquidity` operator pattern but with explicit authorization.

3. **At minimum, document** that `SwapAllowlistExtension` only gates the immediate caller of `pool.swap()` and is incompatible with router-mediated swaps unless the router itself is the intended gate boundary.

---

### Proof of Concept

```solidity
function testSwapAllowlistBypassViaRouter() public {
    // Pool is deployed with SwapAllowlistExtension as beforeSwap hook.
    // Alice is the only allowlisted swapper.
    swapExtension.setAllowedToSwap(address(pool), alice, true);

    // Admin also allowlists the router so Alice can swap through it.
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Bob is NOT allowlisted. Direct swap reverts.
    vm.prank(bob);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(bob, true, int128(1000), 0, "", "");

    // Bob routes through the router — extension sees sender=router (allowlisted) → passes.
    deal(token0, bob, 10_000);
    vm.startPrank(bob);
    IERC20(token0).approve(address(router), type(uint256).max);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool:             address(pool),
            recipient:        bob,
            tokenIn:          token0,
            zeroForOne:       true,
            amountIn:         1000,
            amountOutMinimum: 0,
            priceLimitX64:    0,
            deadline:         block.timestamp + 1,
            extensionData:    ""
        })
    );
    // Bob successfully swapped against the "restricted" pool.
    assertGt(amountOut, 0);
}
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which checks `allowedSwapper[msg.sender][sender]` where `sender` is the router address for all router-mediated swaps, not the actual end user. [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
