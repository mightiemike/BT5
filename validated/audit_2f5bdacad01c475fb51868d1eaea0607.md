Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End-User Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the router contract is `msg.sender` inside the pool, so the allowlist gates the router address rather than the economic actor. This creates two mutually exclusive failure modes: either all router-mediated swaps revert (broken core functionality), or any user can bypass the curated allowlist entirely by routing through the router.

## Finding Description
In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The result is that `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][end_user]`. The same flaw applies to `exactInput`, `exactOutput`, and `exactOutputSingle`. The `DepositAllowlistExtension` does not share this bug because it gates on `owner` (the position owner passed explicitly), which is set to the actual user by the liquidity adder: [5](#0-4) 

## Impact Explanation
A pool admin deploys a curated pool (e.g., institutional RWA pool, KYC-gated) and configures `SwapAllowlistExtension` to restrict swaps to approved addresses. Two failure modes exist: (1) if the router is not allowlisted, all router-mediated swaps revert for every user including legitimately allowlisted ones — broken core swap functionality; (2) if the router is allowlisted to let legitimate users use the router, every user on the internet can call `exactInputSingle` through the router and bypass the allowlist entirely, executing swaps against the pool's LP assets as unauthorized counterparties. LP principal is directly at risk from unauthorized counterparties in a pool designed to trade only with trusted parties.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint for end-users. No privileged access, special setup, or non-standard token behavior is required. Any user who reads the router interface can exploit this in a single transaction. The bypass is unconditional and reachable on every router-mediated swap.

## Recommendation
`SwapAllowlistExtension` must gate on the economic actor — the original end-user — not the direct caller of `pool.swap()`. The router already stores `msg.sender` in transient storage for the payer context. The fix should expose the real originator in `extensionData` or a dedicated transient slot so the extension can read and verify the true initiator. Alternatively, maintain a registry of trusted routers; when `sender` is a known router, require the router to attest the real user in `extensionData` and have the extension decode and verify it.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
// allowedSwapper[pool][alice] = true
// Router is allowlisted to allow alice to use it: allowedSwapper[pool][router] = true

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
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// Bob's swap succeeds despite not being allowlisted
router.exactInputSingle(params); // succeeds for bob
```
The root cause is at `SwapAllowlistExtension.beforeSwap` line 37: `allowedSwapper[msg.sender][sender]` evaluates to `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][bob]` when the router intermediates the call.

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
