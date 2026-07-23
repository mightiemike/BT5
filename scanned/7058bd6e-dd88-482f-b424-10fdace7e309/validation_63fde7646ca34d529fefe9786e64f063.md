### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address to enable router-based swaps, every user — including those the admin intended to block — can bypass the per-user allowlist by routing through the router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. A pool admin who allowlists the router address (the natural step to enable router-based swaps) inadvertently opens the gate for every user, because the router is a shared, permissionless contract.

The extension's NatSpec states it "Gates `swap` by swapper address, per pool": [5](#0-4) 

The "swapper" the admin intends to gate is the end user, but the implementation gates the intermediary router. The two goals — allow router-based swaps AND restrict specific end users — are mutually exclusive under the current design.

### Impact Explanation
Any user blocked by the per-pool swap allowlist can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) on a pool where the router address has been allowlisted. The configured access-control boundary is silently voided for all router-mediated swaps. Pools deployed for curated or permissioned trading (e.g., KYC-gated, institutional-only, or compliance-restricted pools) lose their enforcement guarantee. Unauthorized users gain full swap access to pool liquidity and pricing, which constitutes a broken core pool functionality with direct policy and potential economic impact on LP assets and fee flows.

### Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address. This is the expected operational step for any pool that wants to support the standard periphery swap path. Admins who configure the allowlist to restrict specific users while also enabling router access will unknowingly create the bypass. The router is a public, permissionless contract, so once it is allowlisted, the bypass is reachable by any address with no further preconditions.

### Recommendation
The extension must check the identity of the economic actor (the end user), not the identity of the intermediary contract. Two viable approaches:

1. **Pass the original caller through the router**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of (or in addition to) `sender`.
2. **Check `sender` against the allowlist and reject router addresses explicitly**: Require that `sender` is an EOA or a known non-router contract, so the router cannot be used as a pass-through.

The deposit-side analogue (`DepositAllowlistExtension`) is not affected because it checks `owner` (the position owner explicitly passed by the caller), which is preserved correctly through the liquidity adder path.

### Proof of Concept
```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)      // enable router-based swaps

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → extension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, charlie receives output tokens

Result:
  charlie bypasses the allowlist and swaps successfully.
  Direct pool call by charlie would revert:
    pool.swap(...) [msg.sender = charlie]
      → allowedSwapper[pool][charlie] == false  → NotAllowedToSwap ✗
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
