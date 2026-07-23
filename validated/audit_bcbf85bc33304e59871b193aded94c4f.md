Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router so that individually-allowlisted users can access the pool via the standard UX path, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` (where `msg.sender` is the pool and `sender` is the forwarded caller): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly with no originating-user context: [4](#0-3) 

At that point, `msg.sender` seen by the pool is the router's address, so `sender` delivered to the extension is the router's address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Individually-allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the gate via the router |

No configuration simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` router entry points. [5](#0-4) 

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) is rendered ineffective the moment the router is allowlisted. Any unprivileged EOA can call `MetricOmmSimpleRouter.exactInputSingle` and trade in the restricted pool, breaking the intended access boundary. LPs in such a pool are exposed to adverse-selection or volume from counterparties the pool was explicitly designed to exclude. This is a direct broken-core-functionality finding under the allowlist bypass pivot: "swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router." [6](#0-5) 

## Likelihood Explanation
The router is the primary user-facing entry point. A pool admin who wants allowlisted users to be able to use the router (the normal UX path) will naturally add the router to the allowlist. This is the expected operational action, and it silently opens the gate to all users. No special attacker capability is required — any EOA can call the router. The bypass is repeatable and requires zero privileged access beyond the admin's own expected configuration step. [7](#0-6) 

## Recommendation
The extension should gate the economically relevant actor, not the intermediary. The simplest safe fix: have the router encode `msg.sender` into `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and verify that address when present, falling back to `sender` when no override is present. Alternatively, document that pools using `SwapAllowlistExtension` are incompatible with router-mediated swaps and must be called directly, and enforce this at the extension level by reverting when `sender` is a known router address. [3](#0-2) 

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...)           // msg.sender to pool = router
   → pool calls _beforeSwap(sender=router, ...) // ExtensionCalling forwards router address
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for Bob despite Bob not being allowlisted
5. Direct call: pool.swap(...) with msg.sender=bob
   → allowedSwapper[pool][bob] == false → reverts
   Bob bypasses the gate exclusively through the router.
``` [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
