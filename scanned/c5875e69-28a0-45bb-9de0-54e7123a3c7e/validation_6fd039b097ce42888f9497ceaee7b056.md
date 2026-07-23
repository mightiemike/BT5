### Title
SwapAllowlistExtension checks router address instead of actual swapper, allowing allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. Because `MetricOmmPool.swap` always passes `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is the direct caller of `pool.swap`, the extension sees the **router address** as the swapper — not the actual user. Any pool that allowlists the router (a natural operational choice) becomes fully open to every user, defeating the curation policy entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router itself `msg.sender` inside the pool: [4](#0-3) 

The actual user (`msg.sender` of the router call) is stored only in transient callback context for payment purposes and is never forwarded to the pool as the swap initiator. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

---

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of addresses deploys `SwapAllowlistExtension` and allowlists specific users. To let those users trade through the standard periphery, the admin also allowlists `MetricOmmSimpleRouter`. At that point the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including addresses the admin explicitly never allowlisted. Non-curated users can execute swaps on the restricted pool, draining LP value at oracle-anchored prices the pool was configured to offer only to approved counterparties. This is a direct loss of LP principal and a complete break of the pool's curation invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap entry point documented and shipped with the protocol. Any pool admin who wants allowlisted users to trade conveniently must allowlist the router, which is the natural operational step. The bypass requires no special privilege, no malicious setup, and no non-standard token — only a public call to `router.exactInputSingle` or any other router method.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two sound approaches:

1. **Pass the original initiator through the pool.** Add an explicit `initiator` parameter to `IMetricOmmPoolActions.swap` (separate from `recipient`) and have the router supply `msg.sender` there. The pool forwards it to `_beforeSwap` as a distinct field, and the extension checks that field.

2. **Decode initiator from `extensionData`.** Define a convention where the router prepends the real user address to `extensionData`; the extension decodes and checks it. This avoids a core interface change but requires router cooperation and careful ABI discipline.

Either way, `SwapAllowlistExtension.beforeSwap` must not treat the first `sender` argument as the authoritative swapper identity when that argument can be a trusted intermediary.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the curated user
  allowedSwapper[pool][router] = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][bob]    = false  // bob is NOT curated

Attack (bob):
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...) — msg.sender inside pool = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. extension checks allowedSwapper[pool][router] → true  ✓ (passes)
  5. swap executes; bob receives output tokens at oracle-anchored price

Result:
  bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist guard is silently bypassed for every router-mediated swap.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
