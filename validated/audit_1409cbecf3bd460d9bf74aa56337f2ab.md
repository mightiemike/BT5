### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the **actual user**. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user on the public router.

### Finding Description

**Trace through the call stack:**

1. A user calls `MetricOmmSimpleRouter.exactInputSingle()`. The router stores the real user in transient storage for payment purposes only, then calls `pool.swap(recipient, ...)` directly. [1](#0-0) 

2. Inside `MetricOmmPool.swap()`, the pool calls `_beforeSwap(msg.sender, ...)`. At this point `msg.sender` is the **router**, not the original user. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes `sender` (= router address) as the first argument and dispatches to the extension. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` receives `sender` = router and evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for the mapping key), but `sender` is the **router address**, not the original user. [4](#0-3) 

The router never forwards the original caller's identity to the pool; it only stores it in transient storage for the payment callback. [5](#0-4) 

**Two broken outcomes result from this mismatch:**

- **Bypass path:** If the pool admin allowlists the router address (the only way to let any user swap through the router), `allowedSwapper[pool][router] = true` passes for every user who routes through the router, regardless of whether that user is individually permitted. The allowlist is completely ineffective for router-mediated swaps.
- **Broken functionality path:** If the pool admin allowlists individual users but not the router, those users cannot swap through the supported periphery path at all, because the extension sees the router and reverts.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Any unpermissioned user can execute swaps against the pool, draining LP value at oracle-anchored prices that the pool admin intended to offer only to allowlisted parties. This is a direct loss of LP principal and a complete failure of the pool's curation policy.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery swap path. Any pool that uses `SwapAllowlistExtension` and needs to support router-mediated swaps must allowlist the router, which immediately opens the bypass to all users. The trigger requires no special privileges: any public user calls `exactInputSingle` or `exactInput` on the router pointing at the curated pool.

### Recommendation

The pool must pass the economically relevant actor — the original user — to the extension, not the immediate `msg.sender`. Two approaches:

1. **Router-side:** Have the router encode the original `msg.sender` in `extensionData` and have the extension decode it. This requires a convention between router and extension.
2. **Pool-side:** Add a separate `originator` field to the swap call that the router populates with the real user address, and pass it through `_beforeSwap` to the extension.

The simplest correct fix is for `SwapAllowlistExtension` to read the originator from `extensionData` when `sender` is a known router, or for the pool interface to carry a distinct `originator` address that the router sets to `msg.sender` before calling the pool.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   // admin enables router path
  - allowedSwapper[pool][alice] = true    // alice is the intended user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...)  — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  → passes
  5. Bob's swap executes against the curated pool at oracle prices
     despite bob never being allowlisted

Result: bob receives tokens from the pool at oracle-anchored rates
        that the pool admin intended to offer only to alice.
        LP funds are consumed by an unpermissioned counterparty.
``` [6](#0-5) [7](#0-6) [1](#0-0)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
