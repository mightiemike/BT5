### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Instead of End User, Allowing Complete Allowlist Bypass on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If a pool admin allowlists the router to enable router-mediated swaps for their curated users, every unpermissioned user can bypass the allowlist by calling through the same public router.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the pool's `msg.sender` is the **router**.
3. Pool calls `_beforeSwap(msg.sender, recipient, ...)` = `_beforeSwap(router_address, user_address, ...)`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router_address, ...)`.
5. The extension evaluates:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router**, so the check resolves to `allowedSwapper[pool][router]`.

The extension never sees the actual end user's address. The same misbinding occurs for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` because the pool's direct caller is always the router.

**Bypass path:**

A pool admin who wants their allowlisted users (e.g., Alice, Bob) to be able to use the standard router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the extension's check passes for **any** caller who routes through the router — including Charlie who was never allowlisted.

The pool admin has no way to allowlist specific end users for router-mediated swaps while blocking others, because the extension only sees the router's address.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional partners, etc.) is completely open to any user who routes through `MetricOmmSimpleRouter`. The allowlist policy is nullified the moment the pool admin enables router support. Unauthorized users can execute swaps, drain LP value at oracle prices, and extract fees that were intended only for the curated set of participants.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants their allowlisted users to have a normal UX (deadline, slippage protection, multi-hop) must allowlist the router. The bypass requires no special knowledge: any user calls the public router with the target pool address. The router is already deployed and permissionless.

### Recommendation

Pass the original end user's address through the swap path so the extension can gate on the economically relevant actor. One approach: add a `swapper` field to the `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it. A cleaner approach is to have the pool accept an explicit `swapper` parameter (separate from `msg.sender`) that the router fills with its own `msg.sender`, and pass that to extensions as the gated identity. Either way, the extension must receive the address of the human/contract that initiated the trade, not the address of the intermediary router.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: setAllowedToSwap(pool, alice, true)
  pool admin calls: setAllowedToSwap(pool, router, true)   // required for Alice to use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  Execution:
    router → pool.swap(charlie, ...)          // pool.msg.sender = router
    pool   → _beforeSwap(router, charlie, ...)
    pool   → extension.beforeSwap(sender=router, ...)
    extension checks: allowedSwapper[pool][router] == true  ✓
    swap executes — charlie receives output tokens
```

Alice's allowlist entry (`allowedSwapper[pool][alice]`) is never consulted. The router's entry is the only one that matters, and it is shared by all callers. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
