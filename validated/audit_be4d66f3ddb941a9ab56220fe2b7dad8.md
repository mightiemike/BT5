### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router (a natural setup to enable router-mediated swaps), the allowlist is completely bypassed: every user who can call the public router can swap on the restricted pool, regardless of whether they are individually allowlisted.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` on behalf of the user: [4](#0-3) 

At that point `msg.sender` to the pool is the **router**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end user's address is only present as `recipient` (the output destination), which the extension ignores entirely.

This creates two broken states:

1. **Allowlist bypass**: If the pool admin allowlists the router address (the natural action to permit router-mediated swaps), every user who can call the public router bypasses the per-user restriction. The allowlist becomes a no-op for all router paths.

2. **Broken allowlisted-user flow**: If the admin allowlists specific user addresses instead, those users cannot swap through the router at all (the router is not allowlisted), forcing them to call `pool.swap()` directly and implement the swap callback themselves — an interface not designed for EOAs.

The analog to the HolographERC721 M-19 finding is exact: just as that contract checked `_isApproved` (approved addresses) instead of `isApprovedForAll` (operators), this extension checks the intermediary router address instead of the actual end-user identity, granting the wrong principal the ability to satisfy the guard.

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to enforce KYC, whitelist-only, or institutional-access restrictions receives no protection against any user who routes through `MetricOmmSimpleRouter`. The LP funds in the pool are exposed to swaps from any address, violating the access model the admin configured. Because the router is a public, permissionless contract, the bypass requires no special privilege — any EOA can exploit it.

### Likelihood Explanation

The router is the primary user-facing swap interface for the protocol. Pool admins who want to restrict swaps while still supporting normal UX will naturally allowlist the router. The `SwapAllowlistExtension` NatDoc says it "Gates `swap` by swapper address, per pool," which implies end-user gating; nothing in the interface or documentation warns that router-mediated swaps substitute the router's address for the user's. The mismatch is invisible until the bypass is exercised.

### Recommendation

Replace the `sender` check with a check on the `recipient` parameter (the address that receives swap output), or require the router to forward the originating user's address inside `extensionData` and have the extension decode it. The simplest correct fix for the common case is:

```solidity
function beforeSwap(
    address sender,
    address recipient,   // <-- use this as the identity to gate
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata
) external view override returns (bytes4) {
    address swapper = recipient != address(0) ? recipient : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, document explicitly that the extension gates the **direct caller of `pool.swap()`** and that router-mediated swaps are gated by the router address, so pool admins can make an informed decision.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Admin also calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps (natural setup).
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(recipient=bob, ...)` — `msg.sender` to pool is the router.
6. Pool calls `extension.beforeSwap(sender=router, recipient=bob, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was never allowlisted for, bypassing the access control entirely. [3](#0-2) [5](#0-4) [6](#0-5)

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
