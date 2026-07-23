### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any unprivileged swapper to bypass a curated pool's per-user allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (a necessary step to support router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its guard as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument the pool forwards — which is `msg.sender` of the pool's own `swap` call. [2](#0-1) 

`MetricOmmSimpleRouter` calls `pool.swap(params.recipient, ...)` directly, making the pool's `msg.sender` the router contract, not the end user. [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants to support router-mediated swaps must allowlist the router address. Once the router is allowlisted, the per-user gate is completely open to every caller of the router, regardless of whether they are individually permitted.

The `CallExtension.callExtension` path propagates the revert faithfully, so the guard does execute — but it executes against the wrong identity. [4](#0-3) 

The allowlist mapping is keyed `pool → swapper → bool`, and the admin setter only accepts individual addresses: [5](#0-4) 

There is no mechanism to gate the real end-user identity when the router is the immediate caller.

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institution-only, or whitelist-restricted) that also supports the public router must allowlist the router. Doing so collapses the per-user allowlist to a single binary: either the router is allowed (all users pass) or it is not (no router user passes). Any user not on the allowlist can execute swaps against the pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, bypassing the intended access control. This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path circumvents a configured guard.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool operator who deploys a `SwapAllowlistExtension` and also wants to support the standard periphery router will encounter this conflict. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a standard router call.

---

### Recommendation

The `beforeSwap` hook should receive and check the **economic actor** (the end user), not the immediate caller. Two approaches:

1. **Pass the real user through the router**: Have the router encode the originating `msg.sender` in `extensionData` and have the extension decode and check it. This requires a trusted convention between the router and the extension.
2. **Check `tx.origin` as a fallback**: Acceptable only if the pool explicitly documents that `tx.origin` is the gated identity, which has its own limitations.
3. **Separate the allowlist key**: Gate on `recipient` (the address receiving output tokens) rather than `sender`, since the recipient is harder to spoof and is the economically relevant party in a swap.

The cleanest fix is option 1: the router should forward `msg.sender` in a standardized `extensionData` prefix, and the extension should verify that the pool's `msg.sender` is a trusted router before trusting the decoded user identity.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — Alice is not individually allowlisted.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(alice_recipient, ...)` — pool's `msg.sender` = router.
6. `beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Alice successfully swaps on a pool she was explicitly excluded from.

The extension's own unit tests confirm the check is keyed on the `sender` argument passed by the pool, not on any deeper identity: [6](#0-5) 

All tests use `vm.prank(address(pool))` and pass the swapper as the first argument — exactly the path the router collapses to a single router address in production.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-32)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
    if (result.length < 32) {
      revert InvalidExtensionResponse();
    }
    bytes4 returnedSelector;
    assembly ("memory-safe") {
      returnedSelector := mload(add(result, 32))
    }
    bytes4 expectedSelector;
    assembly ("memory-safe") {
      expectedSelector := mload(add(data, 32))
    }
    if (returnedSelector != expectedSelector) {
      revert InvalidExtensionResponse();
    }
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
