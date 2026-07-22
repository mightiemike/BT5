### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks the router's allowlist status. If the router is allowlisted (which is required for any legitimate user to use it), every disallowed user can bypass the curated pool's swap allowlist by routing through the router.

### Finding Description

**Call chain for a direct swap (correct):**
```
user → pool.swap(...)
  msg.sender = user
  _beforeSwap(sender=user, ...)
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][user] ✓
```

**Call chain via MetricOmmSimpleRouter (broken):**
```
user → router.exactInputSingle(params)
  router → pool.swap(params.recipient, ...)   // msg.sender = router
    _beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address, not the actual user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the router) is allowlisted:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

For legitimate allowlisted users to swap via the router, the pool admin must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including users who are explicitly not on the allowlist. The `sender` parameter that carries the actual user identity is never consulted.

This is confirmed by the project's own audit target description: *"the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity"* and *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

### Impact Explanation
Any user blocked by a curated pool's `SwapAllowlistExtension` can bypass the guard by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The router is a public, permissionless contract. The bypass is unconditional once the router is allowlisted. This breaks the core invariant of curated pools: that only approved counterparties can trade. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or whitelist-only liquidity), this constitutes a direct policy failure with fund-impacting consequences for LPs who deposited under the assumption that only approved swappers could trade against their liquidity.

### Likelihood Explanation
Likelihood is high. The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any user who wants to bypass the allowlist simply calls the router instead of the pool directly. No special privileges, flash loans, or complex setup are required. The only precondition is that the router is allowlisted, which is a necessary operational step for the pool to be usable by legitimate users via the router.

### Recommendation
The extension must check the original user's identity, not the intermediary's. Two viable approaches:

1. **Forward original caller in extensionData:** The router encodes `msg.sender` into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`:** For swap allowlists, the economically relevant actor is the recipient of the output token. The extension could check `recipient` (the second parameter of `beforeSwap`) instead of `sender`. However, `recipient` can also be set to a third party, so this is only correct if the pool admin's intent is to gate output recipients.

3. **Dedicated router-aware allowlist:** The router exposes the original `msg.sender` through a dedicated accessor, and the extension queries it. This requires a trusted router interface.

The cleanest fix is option 1: the router encodes the original caller in `extensionData`, and the extension decodes it when present, falling back to `sender` for direct pool calls.

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router must be allowed for alice to use it
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...) — pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives output tokens from the curated pool
  6. The allowlist is completely bypassed
```

**Relevant code locations:**

`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` from the pool's perspective: [4](#0-3)

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
