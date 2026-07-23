### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. Any admin who allowlists the router to enable legitimate router-mediated swaps simultaneously opens the gate to every non-allowlisted user.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → MetricOmmPool.swap(recipient, ..., extensionData)   // msg.sender = router
     → ExtensionCalling._beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← checked, NOT the actual user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, that caller is the router: [4](#0-3) 

The router never forwards the original `msg.sender` (the actual user) into the pool call. The pool has no mechanism to receive it. The extension therefore evaluates `allowedSwapper[pool][router]` — the router's identity — rather than the actual trader's identity.

The admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Legitimate users cannot swap through the router on this pool |
| **Allowlist the router** | Every non-allowlisted user can bypass the allowlist via the router |

---

### Impact Explanation

Any user who is not on the allowlist can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) targeting an allowlist-gated pool. If the router is allowlisted — the only way to let legitimate users use the router — the extension approves the call based on the router's identity, and the swap executes. The allowlist policy is completely nullified for router-mediated paths, which is the primary supported public entrypoint. This is a direct policy bypass with fund-impacting consequences (e.g., regulatory-gated pools, KYC pools, or pools restricted to specific counterparties).

---

### Likelihood Explanation

The router is the canonical user-facing entrypoint documented in the protocol architecture. Any operator who deploys a pool with `SwapAllowlistExtension` and wants their allowlisted users to be able to use the router must allowlist the router address. This is the expected operational pattern, making the bypass condition highly likely to be present in production deployments.

---

### Recommendation

Pass the original caller's identity through the extension data or as a dedicated parameter so the extension can gate the economic actor rather than the intermediary. One concrete approach: have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router. Alternatively, the pool could accept an explicit `originator` parameter that the router populates with `msg.sender`, and extensions gate on `originator` instead of `sender`.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists only user1 for direct swaps
ext.setAllowedToSwap(pool, user1, true);
// Admin also allowlists the router so user1 can use it
ext.setAllowedToSwap(pool, address(router), true);

// Exploit: attacker (not on allowlist) routes through the router
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✓ Swap succeeds — extension saw sender=router (allowlisted), not attacker
// Allowlist policy is bypassed
```

The extension evaluates `allowedSwapper[pool][router] == true` and permits the swap. The attacker's identity is never checked. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
