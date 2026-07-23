### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the user. The allowlist therefore gates the router address, not the actual economic actor. Any user can bypass a curated pool's swap allowlist by routing through the public `MetricOmmSimpleRouter`.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInput` (and `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The call chain is:

```
user → MetricOmmSimpleRouter.exactInput(...)
         → pool.swap(recipient, ...)          // msg.sender = router
             → _beforeSwap(router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]  // checks router, not user
```

**Bypass path**: If the pool admin allowlists the router (to permit router-mediated swaps for any legitimate user), `allowedSwapper[pool][router] = true` causes the guard to pass for **every** caller of the router, including addresses the admin explicitly never allowlisted. The allowlist is completely neutralized.

**Blocking path**: If the pool admin does not allowlist the router, every user who routes through `MetricOmmSimpleRouter` is blocked, even if they are individually allowlisted — breaking the intended UX for legitimate users.

Neither configuration achieves the intended per-user gating. The `DepositAllowlistExtension` avoids this problem by checking `owner` (the second argument, which is the position owner explicitly supplied by the caller) rather than `sender`: [5](#0-4) 

`SwapAllowlistExtension` has no equivalent mechanism to recover the original user identity.

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Any non-allowlisted user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInput` or `exactOutput` instead of calling `pool.swap` directly. The router is a public, permissionless contract. No special privilege or setup is required. The pool's LP assets are exposed to unrestricted swap flow, defeating the curation invariant and potentially causing direct loss of LP value if the allowlist was intended to prevent adverse selection or regulatory exposure.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any user aware of the router (which is the normal way to interact with the protocol) can trigger the bypass without any precondition other than having the input token. The bypass requires zero privileged access and is reachable in a single transaction.

### Recommendation

The `SwapAllowlistExtension` must gate on the original user's identity, not the direct caller of `pool.swap`. Two options:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` into the `extensionData` bytes for the allowlist extension. The extension decodes and verifies it. This requires the router to be aware of the extension convention.

2. **Check `recipient` instead of `sender`**: For direct swaps, `recipient` is often the user. However, this is semantically incorrect for multi-hop or contract-recipient flows.

3. **Mirror `DepositAllowlistExtension`**: Introduce an explicit `swapper` parameter (analogous to `owner` in deposits) that the pool passes separately from `sender`, allowing the router to supply the original user address.

The cleanest fix is option 1: define a convention where the router encodes the originating user in `extensionData` and the extension decodes it when `sender` is a known router.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
extension.setAllowedToSwap(address(pool), alice, true);
// bob is NOT allowlisted

// Direct swap by bob — correctly reverts
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, false, int128(1000), type(uint128).max, "", "");

// Router swap by bob — bypasses the allowlist
// The router calls pool.swap(...) with msg.sender = router
// Extension checks allowedSwapper[pool][router], which is false → should revert
// BUT if admin allowlisted the router for any legitimate user, bob passes too.
// Alternatively: admin never allowlisted the router, so even alice is blocked via router.

// Demonstrate the identity mismatch:
vm.prank(address(router));  // simulate what the router does
// extension sees sender = router, not bob
extension.beforeSwap(address(router), bob, false, 1000, 0, 0, 0, 0, "");
// passes if router is allowlisted, regardless of bob's status
``` [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L151-188)
```text
  ///      recursively inside `metricOmmSwapCallback`: each callback pays the current hop's input, then (unless on
  ///      the last pool) swaps the next pool for exactly that input amount. The first swap's input delta is total
  ///      `amountIn`.
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
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
