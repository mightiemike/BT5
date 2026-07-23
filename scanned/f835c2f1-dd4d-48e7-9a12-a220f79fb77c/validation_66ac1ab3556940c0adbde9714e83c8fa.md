### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool passes `msg.sender = router` as `sender` to the extension. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every unprivileged user can bypass the swap restriction by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` (the direct caller) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the router, not the original user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original `msg.sender` to the pool in any way the extension can observe: [4](#0-3) 

The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot use the router at all — broken core functionality |
| **Yes** | Every non-allowlisted user bypasses the restriction by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd, institutional, or otherwise curated addresses is fully bypassed. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the restricted pool. The extension sees the router address as the swapper and passes the check if the router is allowlisted. The user receives pool output tokens they were never permitted to receive, violating the pool's access-control invariant and potentially draining liquidity reserved for curated participants.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist, which immediately opens the bypass to all users. The attack requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` (the first argument) only for direct pool calls; require the router to be absent from the allowlist and instead check a user-identity field**: Redesign the extension so that when `sender` is a known router, it reads the actual user from a verified field in `extensionData`.

The simplest safe default is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is not an EOA or a known non-router contract, until a proper user-forwarding mechanism is in place.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists alice (KYC'd user) and the router (so alice can use it).
// Bob (not allowlisted) bypasses the restriction:

// 1. Bob calls router, NOT pool directly.
router.exactInputSingle(ExactInputSingleParams({
    pool:           restrictedPool,
    recipient:      bob,
    zeroForOne:     true,
    amountIn:       1e18,
    amountOutMinimum: 0,
    priceLimitX64:  0,
    deadline:       block.timestamp,
    extensionData:  ""
}));

// Inside pool.swap():
//   _beforeSwap(msg.sender = router, ...)
//   Extension checks: allowedSwapper[pool][router] == true  ← passes
//   Bob receives token1 output despite never being allowlisted.
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
