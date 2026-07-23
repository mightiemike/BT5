### Title
`SwapAllowlistExtension` checks router address instead of actual user — allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual user. A pool admin who allowlists the router to let their curated users use the standard periphery path inadvertently opens the pool to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the **router address**, not the originating user. The extension evaluates `allowedSwapper[pool][router]` — a single binary flag for the entire router — rather than `allowedSwapper[pool][actualUser]`.

This creates two mutually exclusive failure modes:

1. **Allowlist bypass (fund-impacting):** The pool admin allowlists the router so that their curated users can use the standard periphery. Because the router is a public, permissionless contract, every user on the network can now call `exactInputSingle` and pass the allowlist check. The curated pool is effectively open to all.

2. **Broken core functionality:** The pool admin does not allowlist the router. Every allowlisted user who attempts to swap through the router is rejected with `NotAllowedToSwap`, even though they are individually permitted. The only usable path is a direct `pool.swap` call, which requires the caller to implement `IMetricOmmSwapCallback` themselves.

### Impact Explanation

Under failure mode 1, any unprivileged user can trade on a pool that was designed to restrict access to specific counterparties. LP funds are exposed to uninvited traders who may exploit oracle-price windows, front-run, or drain value from bins that were priced for a known, trusted set of swappers. This is a direct loss of LP principal and a swap conservation failure (the pool receives input from actors the pool admin explicitly excluded).

Under failure mode 2, the allowlisted users' only supported periphery path (`MetricOmmSimpleRouter`) is permanently broken for that pool, constituting broken core pool functionality.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and shipped with the protocol. Pool admins who configure `SwapAllowlistExtension` and also want their users to use the router will naturally allowlist the router address, triggering the bypass. The trigger requires no privileged action beyond the pool admin's own intended configuration; any unprivileged user can then exploit it by calling the public router.

### Recommendation

The `sender` argument forwarded to `beforeSwap` must represent the **economic actor** — the address that initiated the swap and will bear its consequences. Two complementary fixes:

1. **Short term:** In `MetricOmmSimpleRouter`, pass the originating `msg.sender` as the `recipient`-equivalent identity through a dedicated field, or use a separate `swapper` argument in the pool's `swap` signature so extensions can distinguish the real initiator from the intermediary.

2. **Alternatively:** `SwapAllowlistExtension` should not rely solely on the `sender` hook argument. It could require the router to forward the real user identity in `extensionData`, and verify that identity against the allowlist. The extension would then check `allowedSwapper[pool][abi.decode(extensionData, (address))]` and require the decoded address to match a signed or callback-verified claim.

3. **Long term:** Document clearly that `sender` in all extension hooks equals `msg.sender` of the pool call, not the originating EOA, so extension authors do not assume it represents the end user when a router is in the call path.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended curated user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use periphery

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  Call chain:
    router.exactInputSingle()
      → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes, bob receives output tokens

Result:
  bob, who is not on the allowlist, successfully swaps on the curated pool.
  The allowlist guard is silently bypassed through the public router.
``` [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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
