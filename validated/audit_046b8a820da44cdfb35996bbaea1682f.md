### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End-User Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of `pool.swap()` as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the end-user. The allowlist therefore gates the router address, not the economic actor. Any user can bypass a curated swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput`.

### Finding Description

**Call path:**

```
User (not allowlisted) 
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap()`, `sender` is the router address: [4](#0-3) 

The pool admin allowlists specific EOAs (e.g., KYC-verified users), but the check resolves to `allowedSwapper[pool][router]`. Two failure modes exist:

1. **Router not allowlisted**: All router-mediated swaps revert for every user, including legitimately allowlisted ones — broken core functionality.
2. **Router allowlisted** (to let legitimate users use the router): Every user on the internet can call `exactInputSingle` through the router and bypass the allowlist entirely.

Neither configuration achieves the intended policy. The `DepositAllowlistExtension` does **not** share this bug because it gates on `owner` (the position owner passed explicitly), which the `MetricOmmPoolLiquidityAdder` correctly sets to the actual user: [5](#0-4) 

### Impact Explanation

A pool admin deploys a curated pool (e.g., institutional RWA pool, KYC-gated) and configures `SwapAllowlistExtension` to restrict swaps to approved addresses. Any non-allowlisted user routes through `MetricOmmSimpleRouter` and executes swaps at oracle prices against the pool's LP assets. LP principal is directly at risk from unauthorized counterparties. This is a direct loss of curation policy and potentially of LP funds if the pool was designed to trade only with trusted counterparties.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented for end-users. Any user who reads the router interface can exploit this without any privileged access, special setup, or non-standard token behavior. The bypass is unconditional and reachable in a single transaction.

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** — the original end-user — not the direct caller of `pool.swap()`. Two options:

1. **Pass the original initiator through the router**: The router stores `msg.sender` in transient storage (already done for the payer context). Expose it in `extensionData` or a dedicated transient slot so the extension can read the true originator.
2. **Check `sender` against the allowlist only when `sender` is not a known router**: Maintain a registry of trusted routers; when `sender` is a router, require the router to attest the real user in `extensionData`.

The simplest safe fix is for the pool to pass the original `tx.origin`-equivalent through a signed or transient-storage-attested field, or for the router to include the real user in `extensionData` and for the extension to decode and verify it.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
// allowedSwapper[pool][alice] = true

// Attack: bob (not allowlisted) calls the router
MetricOmmSimpleRouter.ExactInputSingleParams memory params = MetricOmmSimpleRouter.ExactInputSingleParams({
    pool: curated_pool,
    tokenIn: token0,
    recipient: bob,
    deadline: block.timestamp + 1,
    amountIn: 1_000e6,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    extensionData: ""
});

// pool.swap() is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router]
// If router is allowlisted (or allowAllSwappers[pool] == true), bob's swap succeeds
// Bob has bypassed the curated allowlist entirely
router.exactInputSingle(params);  // succeeds for bob
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37: `allowedSwapper[msg.sender][sender]` where `sender` is the router, not the end-user. [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
