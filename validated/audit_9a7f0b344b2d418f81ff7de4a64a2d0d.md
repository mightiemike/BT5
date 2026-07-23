Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the router — not the original user. When a pool admin allowlists the router to enable router-mediated swaps for permissioned users, every unpermissioned user can bypass the curated allowlist by routing through `MetricOmmSimpleRouter`. This renders the allowlist completely inert in the natural operational configuration.

## Finding Description
The call chain is confirmed by production code:

**Step 1:** `MetricOmmPool.swap` passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the original EOA: [3](#0-2) 

**Step 4:** `MetricOmmSimpleRouter.exactInputSingle` stores `msg.sender` in transient storage only for payment purposes (`_setNextCallbackContext`), and never forwards the original caller's identity to the pool as the canonical swapper: [4](#0-3) 

The wrong actor (`address(router)`) is bound as `sender` throughout the entire hook path. No existing guard in the extension or pool corrects this. The `allowAllSwappers` escape hatch is a separate flag and does not mitigate the wrong-actor binding.

## Impact Explanation
Two broken states arise. The fund-impacting state (State A) is the natural operational configuration: the pool admin must allowlist the router so that allowlisted users can trade via the router. Once the router is allowlisted, `allowedSwapper[pool][router] = true`, and the check at line 37 of `SwapAllowlistExtension.sol` passes for **any** caller — including non-KYC'd or non-permissioned addresses — because the extension sees `sender = router`, which is allowlisted. The allowlist provides zero protection. Any user can trade on a pool intended to be restricted to permissioned addresses, causing direct loss of curation integrity and potential regulatory/fund-safety failure for the pool's LPs. This meets the "broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" and "admin-boundary break" impact criteria.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint; pool admins are expected to allowlist it.
- Allowlisting the router is the only way to let allowlisted users trade via the router, so pool admins will do it.
- Once the router is allowlisted, the bypass requires zero privilege — any EOA calls `exactInputSingle` with no special setup.
- The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` since all four call `pool.swap(...)` with `msg.sender = router`. [5](#0-4) 

## Recommendation
The extension must check the economically relevant actor, not the intermediary. Two approaches:

1. **Pass original caller via `extensionData`:** The router encodes `msg.sender` into `extensionData` (or a dedicated field), and `SwapAllowlistExtension.beforeSwap` decodes and verifies it. This requires no change to the core pool interface.

2. **Extend the pool's `swap` signature:** Add an explicit `swapper` address parameter that the router populates with its `msg.sender`, and pass it through `_beforeSwap` as the canonical identity for extension checks.

Either fix must ensure the extension checks the original EOA, not the intermediary contract.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router so allowedUser can trade via router.
extension.setAllowedToSwap(pool, allowedUser, true);
extension.setAllowedToSwap(pool, address(router), true); // required for router to work

// Attack: bannedUser bypasses allowlist via router
vm.prank(bannedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bannedUser,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds — extension checked allowedSwapper[pool][router] = true
// bannedUser traded on a curated pool without being allowlisted
```

The `beforeSwap` hook receives `sender = address(router)` (confirmed at `MetricOmmPool.sol` L231), which is allowlisted, so the check at `SwapAllowlistExtension.sol` L37 passes for `bannedUser`. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
