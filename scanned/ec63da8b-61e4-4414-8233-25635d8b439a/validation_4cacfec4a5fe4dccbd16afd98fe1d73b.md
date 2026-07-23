### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — bypasses the per-user gate.

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router stores the original user's address only in transient callback context (for token pulls), but never forwards it to the pool or to any extension: [4](#0-3) 

The protocol's own audit-target document explicitly identifies this as the invariant that must hold: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties) must also allowlist the router address to support the standard periphery UX. Once the router is allowlisted, the allowlist check passes for **every** user who calls any router swap function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), regardless of whether that user is individually allowlisted. Non-allowlisted users can freely trade on pools that were designed to exclude them, draining LP value or executing trades the pool admin explicitly prohibited.

The secondary failure mode is the inverse: if the admin does **not** allowlist the router, individually allowlisted users cannot use the router at all, breaking the core swap flow for the intended participants.

### Likelihood Explanation

The router is the primary public entrypoint for swaps. Any pool that uses `SwapAllowlistExtension` and also wants router support will allowlist the router — this is the expected operational pattern. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. Any unprivileged user can trigger it by calling a standard router function.

### Recommendation

The router must forward the original caller's identity to the pool so extensions can gate on it. Two concrete options:

1. **Pass the original user as `sender` to `pool.swap`**: Add a `sender` parameter to `IMetricOmmPoolActions.swap` (or use a transient-storage slot the pool reads before calling extensions) so the pool can forward the true initiator to hooks.

2. **Read the payer from transient storage inside the extension**: Have the router write the original `msg.sender` to a well-known transient slot before calling the pool, and have `SwapAllowlistExtension` read that slot when `msg.sender` (the pool's caller) is a recognized router.

Option 1 is cleaner and does not require extensions to know about specific router addresses.

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists only `alice` and the router (to enable router UX).
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted.
// Direct pool call from bob reverts:
vm.prank(bob);
pool.swap(...);  // ← reverts NotAllowedToSwap (bob not in allowedSwapper)

// But router call from bob succeeds:
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ↑ passes: pool sees msg.sender=router, extension checks allowedSwapper[pool][router]=true
// bob successfully swaps on a pool he should be excluded from.
```

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
