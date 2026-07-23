### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any user can bypass a curated pool's swap allowlist by routing through the router if the router address is allowlisted, or allowlisted users are silently blocked from using the router if only their own addresses are allowlisted.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The actual user who initiated the router call is stored only in the router's transient callback context (`_getPayer()`), never forwarded to the pool or the extension. The extension has no way to recover the real user identity.

**Two exploitable paths arise:**

**Path A — Allowlist bypass:** The pool admin allowlists the router address (e.g., to let their own users trade via the router). Because `allowedSwapper[pool][router] == true`, every user who calls the router passes the check, regardless of whether they were individually approved. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and trade on a pool that was supposed to be curated.

**Path B — Allowlisted-user DoS:** The pool admin allowlists specific user addresses but does not allowlist the router. Those users call the router; the extension checks `allowedSwapper[pool][router] == false` and reverts with `NotAllowedToSwap`. Allowlisted users cannot use the supported periphery path at all, breaking core swap functionality for the intended participants.

### Impact Explanation

Path A is the fund-impacting scenario. A curated pool may carry favorable pricing, exclusive LP terms, or restricted access for regulatory or business reasons. Once the router is allowlisted (the only way to let any user reach the pool via the router), the allowlist is completely open to the public. Unauthorized users can drain LP value through arbitrage, access below-market pricing, or violate the pool's access policy. This is a direct loss of LP principal and a broken core invariant: the allowlist no longer gates the economically relevant actor.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also expects users to swap through `MetricOmmSimpleRouter` is affected. The router is the primary supported periphery path. A pool admin who allowlists the router to enable router-mediated swaps triggers the bypass immediately and unconditionally for all users. No special timing, oracle manipulation, or privileged access is required — a single public `exactInputSingle` call from any address suffices.

### Recommendation

The extension must receive the original user identity, not the intermediary router address. Two approaches:

1. **Pass the real initiator through the extension payload.** The router encodes the actual `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` against a router registry and fall back to `extensionData`.** If `sender` is a known router, decode the real user from `extensionData` and check that address instead.

3. **Require direct pool calls for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject extension configurations that pair a swap allowlist with a mutable price provider or router-compatible setup).

The simplest safe fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData` for allowlist-aware pools, and the extension decodes the real user when `sender` matches a known router.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Unprivileged address `attacker` (never individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)` with `msg.sender = router`.
5. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`.
6. Extension checks `allowedSwapper[pool][router] == true` → passes.
7. `attacker` successfully swaps on a pool they were never authorized to access.

Alternatively, for Path B:
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` (individual user, not the router).
2. `alice` calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
3. Extension checks `allowedSwapper[pool][router] == false` → `NotAllowedToSwap` revert.
4. `alice` cannot use the router despite being individually allowlisted.

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
