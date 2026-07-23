Looking at the `SwapAllowlistExtension` and how the router calls the pool, I can identify a clear analog.

In `SwapAllowlistExtension.beforeSwap()`, the guard checks `sender` — which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` = router address, not the actual user. This is structurally identical to the external report: a configured guard operates on the wrong address, causing either a bypass or a DoS depending on how the allowlist is configured.

---

### Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass or DoS for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. This produces two mutually exclusive failure modes: if the router is allowlisted, every user bypasses the per-user gate; if the router is not allowlisted, every allowlisted user is silently blocked from using the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension via `CallExtension.callExtension`). `sender` is whatever `MetricOmmPool.swap()` received as its own `msg.sender` and forwarded to `_beforeSwap`:

```solidity
// ExtensionCalling.sol L149-177 — pool passes msg.sender as sender
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

At this point `msg.sender` inside `pool.swap()` is the router, so `sender` forwarded to `beforeSwap` is the router address. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin has no way to simultaneously (a) allow legitimate users to swap through the router and (b) block non-allowlisted users from doing the same, because the router is a single shared address.

---

### Impact Explanation

**Bypass path (router is allowlisted):** A pool admin allowlists the router so that their approved users can reach the pool through the standard periphery. Any unpermissioned address can then call `router.exactInputSingle()` and pass the `beforeSwap` check, because the check resolves to `allowedSwapper[pool][router] == true`. The per-user allowlist is completely defeated for every router-mediated swap.

**DoS path (router is not allowlisted):** A pool admin allowlists specific EOAs or contracts for direct `pool.swap()` calls. Those allowlisted users cannot use the router at all — every router call reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router] == false`. The standard swap flow is broken for all allowlisted users who rely on the periphery.

Both outcomes are fund-impacting: the bypass allows unauthorized trading on curated pools; the DoS prevents authorized LPs and traders from executing swaps through the only supported periphery path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any user who calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` triggers this path. No special setup is required beyond using the standard router. The condition is reachable on every router-mediated swap on any pool that has `SwapAllowlistExtension` active.

---

### Recommendation

The extension must gate on the economic actor, not the transport layer. Two options:

1. **Pass the original user through the router.** Add a `swapper` field to the router's swap parameters and forward it as `callbackData` or a dedicated field so the pool can pass the true initiator to `beforeSwap`. This requires a coordinated change to the pool interface or a wrapper convention.

2. **Check `sender` only when it is not a known router; otherwise check the payer stored in transient storage.** The router already stores the original payer in transient storage (`_getPayer()`). The extension could read that value via a router-specific interface when `sender` is a recognized router address.

The simplest safe fix is option 1: the router passes `msg.sender` (the user) as an explicit field, and the pool forwards it as the `sender` argument to extensions, replacing the current `msg.sender`-of-`pool.swap()` forwarding.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so users can trade
  alice is NOT in allowedSwapper[pool]

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, ...)
    → msg.sender inside pool.swap() == router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router)
    → allowedSwapper[pool][router] == true  ✓
  alice's swap executes — allowlist bypassed

Alternatively (DoS):
  admin calls setAllowedToSwap(pool, alice, true)   // alice allowlisted, router NOT listed
  alice calls router.exactInputSingle({pool: pool, ...})
    → allowedSwapper[pool][router] == false
    → revert NotAllowedToSwap()
  alice cannot use the router despite being allowlisted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-32)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
    if (result.length < 32) {
      revert InvalidExtensionResponse();
    }
    bytes4 returnedSelector;
    assembly ("memory-safe") {
      returnedSelector := mload(add(result, 32))
    }
    bytes4 expectedSelector;
    assembly ("memory-safe") {
      expectedSelector := mload(add(data, 32))
    }
    if (returnedSelector != expectedSelector) {
      revert InvalidExtensionResponse();
    }
  }
```
