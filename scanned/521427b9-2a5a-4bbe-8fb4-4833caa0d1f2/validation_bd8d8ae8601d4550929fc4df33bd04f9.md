### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When `MetricOmmSimpleRouter` intermediates a swap, the pool receives `msg.sender = router`, passes it as `sender` to the extension, and the extension checks the router's address — not the actual end-user's address. Any user who routes through the router bypasses the per-user allowlist entirely once the router address is allowlisted (which is required for router-based swaps to function at all).

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` encodes this value and calls the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly: [4](#0-3) 

So when a user calls the router, the call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
           → pool.swap()   [msg.sender = router]
               → _beforeSwap(sender = router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender = router)
                       → checks allowedSwapper[pool][router]
```

The extension never sees the actual user's address. It checks the router's address.

This creates two broken states:

1. **Allowlist bypass**: The pool admin must allowlist the router address for any router-based swap to succeed. Once `allowedSwapper[pool][router] = true`, every user — including those the admin explicitly excluded — can swap by routing through `MetricOmmSimpleRouter`. The per-user allowlist is completely defeated.

2. **Broken legitimate access**: If the admin allowlists individual user EOAs but not the router, those users cannot swap through the router even though they are explicitly permitted. The router-based swap path is unusable for allowlisted users.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner passed explicitly), not `sender` (the direct pool caller): [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no actual restriction once the router is allowlisted. Any unprivileged user can execute swaps against the restricted pool by calling the router, bypassing the admin-configured access control. This is an admin-boundary break: the pool admin's allowlist configuration is rendered ineffective by an unprivileged path through the periphery router.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and also expects users to use the router will encounter this issue. The pool admin must allowlist the router to enable router-based swaps, which simultaneously opens the bypass to all users. The trigger requires no special privileges — any EOA can call the router.

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the actual end-user rather than the direct pool caller. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and trust.

2. **Check `sender` against a router-aware allowlist**: Maintain a separate mapping of trusted routers; when `sender` is a trusted router, require the router to attest the real user (e.g., via `extensionData`).

3. **Allowlist at the router level, not the pool level**: Remove `SwapAllowlistExtension` from the swap path and enforce access control in a router wrapper that validates the caller before forwarding to the pool.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Router must also be allowlisted for router-based swaps to work
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted — direct call reverts
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// bob bypasses the allowlist via the router — succeeds
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// bob's swap succeeds — allowlist bypassed
```

The extension checks `allowedSwapper[pool][router]` (true), so bob's swap is permitted despite bob not being on the allowlist. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L121-125)
```text
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
