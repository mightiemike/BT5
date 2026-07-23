### Title
SwapAllowlistExtension gates on router address instead of end-user, enabling full allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `sender` — the `msg.sender` of `pool.swap()` — against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently opens the pool to every user who calls any `exact*` function on the router, completely bypassing the per-user restriction.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs this check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument passed by the pool — which is `msg.sender` of `pool.swap()`. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to the extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly with no forwarding of the original caller's identity: [3](#0-2) 

So `msg.sender` of `pool.swap()` is the router, and `sender` in the extension becomes the router's address. The allowlist check resolves to `allowedSwapper[pool][router]`.

The pool admin is left with only two choices:

1. **Allowlist specific users** → direct swaps work, but router-mediated swaps are blocked for everyone (including allowlisted users), because `sender = router` is not in the allowlist.
2. **Allowlist the router** → all users can swap through the router, bypassing the per-user allowlist entirely.

There is no mechanism to allowlist specific users for router-mediated swaps. The router's address is the only identity the extension sees. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The `ExtensionCalling._beforeSwap` dispatcher faithfully passes `msg.sender` of `pool.swap()` as `sender`, so the mismatch is structural, not a dispatcher bug: [5](#0-4) 

### Impact Explanation

Any user can bypass the swap allowlist by routing through the public `MetricOmmSimpleRouter`. If the pool admin allowlists the router — a natural and expected configuration for pools that want to support router-mediated swaps for their allowlisted users — every user gains unrestricted swap access. Consequences include:

- **Compliance bypass**: KYC/AML-restricted pools become accessible to non-KYC'd users.
- **Unauthorized arbitrage**: Non-allowlisted arbitrageurs can drain LP value through the router.
- **Pool manipulation**: Unauthorized actors can move the oracle-driven bin cursor, affecting all LPs.

This is a direct loss of LP principal through unauthorized swap execution in a pool whose access control is silently nullified.

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected step for any pool that wants to support router-mediated swaps for its allowlisted users. The admin has no way to achieve per-user allowlisting for router paths, so allowlisting the router is the only path forward — and it silently opens the pool to all users. The trigger is a routine admin action, not an exotic attack setup.

### Recommendation

The `SwapAllowlistExtension` must gate on the economic actor, not the intermediary. Two approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` in `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Separate router allowlist**: Add a second mapping `allowedRouter` that, when the `sender` is a known router, falls back to checking the `recipient` or a user identity embedded in `extensionData`.

At minimum, the extension's NatSpec and admin documentation must warn that allowlisting the router grants unrestricted swap access to all router users.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, allowedUser, true).
   → allowedUser can swap directly; all others are blocked.
3. Pool admin calls setAllowedToSwap(pool, router, true)
   to enable router-mediated swaps for allowedUser.
4. unauthorizedUser calls MetricOmmSimpleRouter.exactInputSingle(
       pool, ..., recipient=unauthorizedUser, ...
   ).
5. Router calls pool.swap(...) → msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
8. unauthorizedUser's swap executes successfully in the restricted pool.
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
