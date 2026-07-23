### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the actual end-user. If the pool admin allowlists the router (a natural step to let permitted users access the router), every unpermitted user can bypass the individual allowlist by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

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
``` [1](#0-0) 

The pool's `swap()` and `simulateSwapAndRevert()` both forward `msg.sender` as the `sender` argument into `_beforeSwap`:

```solidity
// MetricOmmPool.sol (simulateSwapAndRevert, same pattern as swap)
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value verbatim into the extension call:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` of the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The result is that `sender` arriving at the extension is the **router address**, not the end-user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., institutional counterparties, KYC-verified wallets) is fully bypassed once the router is allowlisted. Any unpermitted address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension will pass the check because it sees the allowlisted router, not the blocked user. Unauthorized swappers can drain LP value, execute swaps the pool designer intended to block, and circumvent any downstream stop-loss or velocity guard that was predicated on the allowlist keeping bad actors out.

---

### Likelihood Explanation

The scenario is directly reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a natural and expected operational step whenever the admin wants legitimate users to be able to use the standard periphery router. No special role, no malicious setup, and no non-standard token is required. The router is a public, immutable contract that any EOA can call.

---

### Recommendation

The extension must resolve the **ultimate user** rather than the direct pool caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData`; the extension decodes and checks that address. This requires a convention between router and extension.

2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens; gating on `recipient` is harder to spoof via router indirection.

3. **Dedicated router-aware allowlist**: Extend the extension to accept a signed proof or a separate `(router, user)` allowlist entry so the router can attest the real caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = extension1
  - allowedSwapper[pool][router] = true   (admin allowlists the router)
  - allowedSwapper[pool][alice]  = true   (alice is a permitted user)
  - allowedSwapper[pool][bob]    = false  (bob is NOT permitted)

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...)
  3. pool calls extension.beforeSwap(sender=router, ...)
  4. extension checks allowedSwapper[pool][router] == true  → passes
  5. bob's swap executes; allowlist is bypassed

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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
