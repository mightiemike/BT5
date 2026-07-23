### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`. The pool passes `msg.sender` of `pool.swap()` as `sender`. When `MetricOmmSimpleRouter` intermediates the call, `sender` equals the **router address**, not the end user. If the pool admin allowlists the router (the only way to enable router-based swaps at all), every user who routes through `MetricOmmSimpleRouter` bypasses the per-user allowlist entirely.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the caller of `pool.swap` is the router: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`. The pool admin faces a binary choice:

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Router-based swaps fail for **all** users, including allowlisted ones |
| Allowlists the router | Router-based swaps succeed for **all** users, including non-allowlisted ones |

Per-user granularity through the router is structurally impossible. The allowlist guard is misbound to the intermediary, not the economic actor.

---

### Impact Explanation

Any user who is not individually allowlisted can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent — to restrict swap access to specific counterparties — is defeated for all router-mediated volume. Non-allowlisted users trade directly against LP positions on a pool that was configured to exclude them, which can cause LP losses if the restriction was meant to protect against specific trading patterns (e.g., informed flow, regulatory compliance, or protocol-controlled liquidity pools).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entry point. Any user can call `exactInputSingle` or `exactInput` without permission. The only prerequisite for the bypass is that the router is allowlisted on the pool — which is a necessary condition for the pool to be usable via the router at all. A pool admin who deploys a `SwapAllowlistExtension` and wants router-based swaps to work will inevitably trigger this bypass.

---

### Recommendation

The extension must check the **original end user**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the pool's forwarding of `extensionData`, which is already guaranteed by `ExtensionCalling`.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the economically relevant actor is often the recipient. The `recipient` is already passed as the second argument to `beforeSwap` and is set by the end user, not the router.

3. **Dedicated router-aware allowlist**: The router exposes the original `msg.sender` via a transient storage slot that the extension reads directly, removing the dependency on the `sender` argument.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `alice` is allowlisted
extension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it
extension.setAllowedToSwap(address(pool), address(router), true);

// Bob is NOT allowlisted — direct swap reverts
vm.prank(bob);
pool.swap(bob, true, 1000, type(uint128).max, "", ""); // reverts NotAllowedToSwap

// Bob routes through the router — passes because sender = router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Bob's swap succeeds — allowlist bypassed
```

The `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the check passes regardless of who initiated the router call. [5](#0-4) [6](#0-5)

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
