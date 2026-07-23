### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When users swap through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user on the internet the ability to bypass the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is the sole enforcement point for the per-pool swap allowlist:

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

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is set to `msg.sender` of the pool's `swap` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
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

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`:

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
```

So `msg.sender` of `pool.swap` is the **router**, not the user. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable conflict for any pool admin who wants to:
1. Support router-mediated swaps (requires allowlisting the router address), **and**
2. Restrict swaps to a curated set of users (requires the allowlist to check the actual user)

Allowlisting the router satisfies (1) but collapses (2): every user who routes through the router is implicitly allowlisted. Not allowlisting the router satisfies (2) for direct calls but breaks (1): even explicitly allowlisted users cannot use the router.

---

### Impact Explanation

A pool admin who allowlists the router to enable standard periphery access grants every user on the network the ability to swap on a pool that was intended to be curated. LPs on such a pool accepted counterparty risk only from the allowlisted set; the bypass exposes them to arbitrary counterparties, enabling adversarial flow (e.g., informed order flow, sandwich attacks, or directional pressure) that the allowlist was designed to exclude. This is a direct loss-of-LP-value scenario and a complete failure of the configured access-control boundary.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Any pool admin who deploys a curated pool and also wants to support standard tooling (wallets, aggregators, front-ends that use the router) will allowlist the router. The bypass is then reachable by any unprivileged user with a single router call. No special permissions, flash loans, or multi-step setup are required.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` and have the extension decode and check that address. The pool admin would allowlist users, not the router.

2. **Recipient-based check**: Gate on `recipient` instead of `sender` when `sender` is a known router, though this requires the extension to be aware of trusted routers.

The simplest safe fix is approach (1): the router encodes `abi.encode(msg.sender)` into `extensionData`, and the extension decodes it as the authoritative swapper identity.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true)
3. Pool admin does NOT allowlist userB:
       allowedSwapper[pool][userB] == false
4. userB calls:
       router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}))
5. Router calls pool.swap(...) — msg.sender of pool.swap == router.
6. _beforeSwap(sender=router, ...) is dispatched.
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. Swap executes. userB has bypassed the allowlist.
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
