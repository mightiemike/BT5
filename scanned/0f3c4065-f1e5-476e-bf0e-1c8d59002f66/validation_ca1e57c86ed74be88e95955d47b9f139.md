### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating user. If a pool admin allowlists the router address to enable router-mediated swaps for their permitted users, every unprivileged user on the network can bypass the allowlist by calling the same public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct key for the per-pool mapping). `sender` is the first argument, which `ExtensionCalling._beforeSwap` sets to `msg.sender` of the pool's `swap()` call:

```solidity
// ExtensionCalling.sol L88-98
function _beforeAddLiquidity(address sender, ...) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, ...))
    );
}
```

And in `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

So `msg.sender` of `pool.swap()` = router address. The extension receives `sender = router`, not the originating EOA. The router stores the original user in transient storage for the payment callback only — it is never forwarded to the pool or extension as the identity to gate.

**Attack path**: A pool admin deploys a `SwapAllowlistExtension`-guarded pool and allowlists the router address (the natural configuration to let their permitted users swap via the router). Because the router is a public, permissionless contract, any user can call `router.exactInputSingle(...)` and the extension sees `sender = router`, which is allowlisted, and passes the check. The actual originating user is never verified.

The same bypass applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`.

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for pool admins to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). When the router is allowlisted — the only way to support router-mediated swaps for permitted users — the guard becomes completely ineffective: any address on the network can execute swaps against the restricted pool by routing through the public router. This is a direct admin-boundary break: an unprivileged path (the public router) bypasses an access control boundary the pool admin explicitly configured.

### Likelihood Explanation

The likelihood is medium-high. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. This is the natural, expected configuration. The admin has no other option to support router-mediated swaps while keeping the allowlist active. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges or capital requirements.

### Recommendation

The extension must verify the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass originating user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks this value. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` against a router-aware allowlist**: Extend the extension to recognize router addresses and, when `sender` is a known router, require that the `extensionData` carries a signed or otherwise authenticated originating user identity.

3. **Disable router support for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls only.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls: extension.setAllowedToSwap(pool, address(router), true)
    (intending to allow permitted users to use the router)
  - Pool admin does NOT call: extension.setAllowedToSwap(pool, bob, true)
    (bob is not a permitted swapper)

Attack:
  - bob calls router.exactInputSingle({pool: pool, tokenIn: token0, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    with msg.sender = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  ← passes
  - Bob's swap executes successfully despite not being on the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
