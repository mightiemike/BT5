### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the pool to **any** user routing through the same router, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes `sender` (the immediate pool caller) into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly with itself as `msg.sender`: [4](#0-3) 

So the extension sees `sender = router_address`, not the actual user. The pool admin faces an impossible choice:

- **Do not allowlist the router**: allowlisted users cannot use the standard periphery path at all (broken core functionality).
- **Allowlist the router**: the check becomes `allowedSwapper[pool][router] == true`, which passes for **every** user routing through the router, regardless of whether they are individually allowlisted.

By contrast, `DepositAllowlistExtension` correctly gates on `owner` (the position owner), which is preserved through `MetricOmmPoolLiquidityAdder` because the adder passes the actual owner as a separate argument. The swap path has no equivalent "owner" concept — only `sender` (immediate caller) and `recipient` (output receiver), neither of which identifies the actual economic actor when routing. [5](#0-4) 

### Impact Explanation

A pool admin deploying `SwapAllowlistExtension` to create a permissioned pool (e.g., KYC-gated, institutional-only) cannot simultaneously enforce per-user identity checks and support the standard `MetricOmmSimpleRouter` periphery path. If they allowlist the router to unblock their legitimate users, any unprivileged address can bypass the allowlist entirely by routing through the same router. This constitutes a complete admin-boundary break: the pool's curation policy is rendered ineffective through a supported public entrypoint.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and also wants to support router-mediated swaps will encounter this issue. The bypass requires no special privileges — any address can call `exactInputSingle` on the router. The pool admin's only recourse is to never allowlist the router, which forces all allowlisted users to call `pool.swap` directly, breaking the intended UX.

### Recommendation

The `SwapAllowlistExtension` should gate on the **economic actor** rather than the immediate pool caller. Two approaches:

1. **Check `recipient` instead of `sender`**: The recipient is the address that receives swap output and is set by the user, not the router. However, this changes the semantics of "who is allowed to initiate a swap."

2. **Pass the original `msg.sender` through `extensionData`**: The router could encode the original caller in `extensionData`, and the extension could decode and verify it. This requires a trusted router or a signed attestation.

3. **Require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory or extension level.

The cleanest fix is option 2 with a factory-verified router that appends the original caller to `extensionData`, analogous to how `MetricOmmPoolLiquidityAdder` preserves `owner` separately from `msg.sender`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists alice (legitimate user) via setAllowedToSwap(pool, alice, true)
  - Pool admin also allowlists router via setAllowedToSwap(pool, router, true)
    (required so alice can use the standard periphery)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...) with msg.sender=router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - SwapAllowlistExtension is completely bypassed for any user routing through MetricOmmSimpleRouter
  - The pool admin's curation policy is defeated through the supported public periphery path
``` [6](#0-5) [4](#0-3) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
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
