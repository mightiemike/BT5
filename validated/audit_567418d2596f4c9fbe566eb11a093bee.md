### Title
`SwapAllowlistExtension` Per-User Allowlist Fully Bypassed When Router Is Allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for authorized users inadvertently opens the pool to every user, completely defeating the per-user allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no user-identity forwarding: [4](#0-3) 

When a user goes through the router, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`. The extension has no visibility into the actual end-user.

This creates an irresolvable dilemma for the pool admin:

| Admin intent | Admin action | Actual result |
|---|---|---|
| Allow Alice only, direct calls | Allowlist Alice | Alice can swap directly; Alice **cannot** use the router |
| Allow Alice only, via router | Allowlist router | **Every user** can swap via the router |

There is no configuration that simultaneously allows Alice to use the router and blocks Bob from using the router.

### Impact Explanation

Any user can bypass a `SwapAllowlistExtension` guard on a pool by routing through `MetricOmmSimpleRouter` whenever the pool admin has allowlisted the router address. The attacker can execute unrestricted swaps against a pool whose LP depositors expected only vetted counterparties, extracting value at oracle-anchored prices from bins that were priced for a controlled audience. This is a direct loss of LP principal and breaks the core access-control invariant the extension is designed to enforce.

### Likelihood Explanation

Medium. The scenario requires a pool admin to (a) deploy a pool with `SwapAllowlistExtension` and (b) also allowlist the router so that authorized users can trade conveniently. Both steps are natural and expected in a production deployment. The documentation for the extension states it "Gates `swap` by swapper address" without disclosing that the "swapper" is the immediate `pool.swap()` caller, not the end-user, so a pool admin has no obvious signal that allowlisting the router opens the gate to everyone.

### Recommendation

The extension must be able to identify the true end-user when the immediate caller is a trusted intermediary. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` and have the extension decode and check that address instead of (or in addition to) `sender`.
2. **Separate router allowlist**: Add a second mapping `allowedRouter` and, when `sender` is an allowlisted router, require the extension-data-encoded user to be individually allowlisted.

The `DepositAllowlistExtension` avoids this problem by gating on `owner` (the economic beneficiary) rather than `sender` (the payer/operator): [5](#0-4) 

`SwapAllowlistExtension` should adopt an analogous design that separates the identity being gated from the identity of the immediate caller.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)
  pool admin calls setAllowedToSwap(pool, router, true)   ← to let Alice use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  router calls:
    pool.swap(charlie, ...)   // msg.sender = router

  pool calls:
    extension.beforeSwap(sender=router, ...)

  extension evaluates:
    allowedSwapper[pool][router] == true  → passes

  charlie's swap executes against the restricted pool.
```

The allowlist check passes because it sees the router (allowlisted) rather than charlie (not allowlisted). Charlie extracts tokens from the pool at oracle prices with no authorization. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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
