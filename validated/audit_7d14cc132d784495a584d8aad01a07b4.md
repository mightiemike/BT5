### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin adds the router to the allowlist (a natural action to let allowlisted users use the router), every user — including non-allowlisted ones — can bypass the swap allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `sender` arriving at the extension is the router's address, not the end user's address. The allowlist cannot distinguish between different end users who all route through the same router contract.

**The bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users (e.g., Alice).
2. Alice wants to use the router. The admin adds `MetricOmmSimpleRouter` to the allowlist via `setAllowedToSwap(pool, router, true)` — a natural, expected action.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap()` → `_beforeSwap(sender=router, ...)` → `allowedSwapper[pool][router]` → `true`.
5. Bob's swap executes successfully, bypassing the per-user allowlist entirely.

This is the direct analog to the external bug: `initWithdrawOnBehalf` used `msg.sender` (the owner) instead of `onBehalfOf` (the intended address). Here, the allowlist checks `sender` (the router intermediary) instead of the actual end user.

### Impact Explanation

The swap allowlist is the pool admin's primary mechanism to restrict which addresses may trade against the pool. Once the router is added to the allowlist (which is necessary for allowlisted users to use the router), the guard is completely neutralized for all users. Any address can execute swaps against a pool that was intended to be restricted. This breaks the core access-control invariant of the extension framework and constitutes an admin-boundary break: an unprivileged path (routing through the public router) bypasses a configured pool guard.

### Likelihood Explanation

The trigger requires the pool admin to add `MetricOmmSimpleRouter` to the allowlist. This is a natural and expected operational step — without it, even allowlisted users cannot use the router. The pool admin has no way to simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same, because the extension has no visibility into who called the router. Any pool that uses `SwapAllowlistExtension` and also wants router compatibility is vulnerable.

### Recommendation

The extension must check the identity of the actual end user, not the intermediary. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted, which it is (it is a known periphery contract).

2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers; when `sender` is a trusted router, require the real user address to be passed in `extensionData` and check that instead.

3. **Restrict the allowlist to EOAs only and document that router use is incompatible**: Simpler but limits usability.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // Alice is allowlisted
  allowedSwapper[pool][router] = true         // Admin adds router so Alice can use it

Attack:
  Bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  Call chain:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ← passes
    Bob's swap executes. Allowlist bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
