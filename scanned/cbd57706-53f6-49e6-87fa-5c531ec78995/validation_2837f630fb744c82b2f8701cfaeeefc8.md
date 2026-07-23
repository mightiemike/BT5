### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` passes `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is `msg.sender` when it calls the pool, the extension always sees the router address — not the actual user. Any pool admin who allowlists the router to let legitimate users trade through it simultaneously opens the gate to every other user, completely defeating the allowlist.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              └─ _beforeSwap(msg.sender=router, ...)
                   └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        └─ allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address, not the originating user: [3](#0-2) 

The router never forwards the original caller's identity to the pool; it simply calls `pool.swap()` directly: [4](#0-3) 

This creates an inescapable dilemma for any pool admin:

- **Option A — Do not allowlist the router:** Allowlisted users cannot use the router at all (broken core functionality).
- **Option B — Allowlist the router:** Every user on the network can bypass the allowlist by routing through the router.

The same flaw applies to `exactOutputSingle` and `exactInput` (first hop): [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol-internal actors, or whitelisted market makers) loses that protection entirely the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the restricted pool. This is a direct loss of the pool's access-control invariant and constitutes a **High** severity allowlist bypass with direct fund-impact consequences (unauthorized parties drain or manipulate a curated pool's liquidity).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed by the protocol. Any pool admin who wants allowlisted users to have a normal UX will allowlist the router, triggering the bypass. The attack requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call the router.

---

### Recommendation

The pool must propagate the originating user's address through the swap path so extensions can gate on the real actor. Two approaches:

1. **Pass the original caller through `callbackData`:** The router encodes `msg.sender` into `callbackData`; the pool decodes it and passes it as `sender` to extensions instead of `msg.sender`. This requires a protocol-level change to `MetricOmmPool.swap`.

2. **Extension reads `recipient` instead of `sender`:** For the swap allowlist use-case, gate on `recipient` (the address that receives output tokens) rather than `sender`. This is a weaker fix because `recipient` can also be set arbitrarily by the router caller.

The cleanest fix is approach 1: add an explicit `originator` field to the swap interface that the router populates with `msg.sender` and the pool forwards to extensions, analogous to how `addLiquidity` separates `sender` (payer) from `owner` (position holder).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin must also allowlist the router so alice can use it.
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true); // required for alice to use router

// Attack: bob (not allowlisted) calls the router directly.
// Extension sees sender = router (allowlisted) → swap succeeds.
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        tokenIn: address(token0),
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Bob successfully swaps on a pool he is not allowlisted for.
// SwapAllowlistExtension checked allowedSwapper[pool][router] = true
// instead of allowedSwapper[pool][bob] = false.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
