### Title
SwapAllowlistExtension gates the router address instead of the end user, enabling allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which equals `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address rather than the end user's address. If a pool admin allowlists the router to enable router-based swaps for legitimate users, every unprivileged user can bypass the individual allowlist by routing through the router.

### Finding Description

In `SwapAllowlistExtension.beforeSwap`, the allowlist check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument passed by the pool. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument — i.e., the immediate caller of `pool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // sender = immediate caller of pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then forwards this `sender` verbatim to every configured extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` is called by a user, the router calls `pool.swap()` — so `msg.sender` to the pool is the **router**, not the end user:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-based swaps for allowlisted users **must** allowlist the router (`allowedSwapper[pool][router] = true`), because without it, even individually allowlisted users are blocked when they use the router (the extension sees `sender = router`, which is not individually allowlisted). Once the router is allowlisted, any user — including those never individually allowlisted — can bypass the guard by routing through the router.

The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, all of which call `pool.swap()` as `msg.sender = router`. [5](#0-4) 

### Impact Explanation

`SwapAllowlistExtension` is the primary mechanism for curating who can trade on a pool. If the router is allowlisted — a necessary step to support router-based swaps for legitimate users — the allowlist is completely ineffective: any unprivileged user can swap on a pool intended to be restricted. Unauthorized trades execute against LP liquidity, constituting a direct policy bypass with fund-flow consequences on curated pools. [6](#0-5) 

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router. This is a natural and expected action: without it, individually allowlisted users cannot use the router at all (the extension sees `sender = router`, which is not on the per-user list). The admin has no way to simultaneously support router-based swaps for specific users and block non-allowlisted users from using the router. The design forces the admin into a choice that silently opens the pool to all users.

### Recommendation

The extension should gate the actual end user, not the immediate pool caller. Options include:

1. Require the router to forward the original `msg.sender` in `extensionData`, and have the extension decode and verify that address against the allowlist.
2. Add an "originator" field to the pool's swap interface that periphery contracts populate, allowing extensions to check the true initiator.
3. Document clearly that `SwapAllowlistExtension` only gates direct pool callers, and that allowlisting the router opens the pool to all router users.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin allowlists Alice: `allowedSwapper[pool][alice] = true`.
3. Pool admin allowlists the router so Alice can use it: `allowedSwapper[pool][router] = true`.
4. Non-allowlisted user Bob calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — `msg.sender` to the pool is the router address.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` = `true` → passes.
7. Bob's swap executes successfully on a pool he was never individually allowlisted for. [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
