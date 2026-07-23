Audit Report

## Title
`SwapAllowlistExtension` gates the direct pool caller (`sender`) rather than the end user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, not the end user. A pool admin who allowlists the router to enable router-based swaps on a curated pool inadvertently opens the gate to every user, because the extension sees only the router address and cannot distinguish end users.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` here is the pool, `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` stores the real payer (`msg.sender`) in transient storage for its own callback, then calls `pool.swap()` directly, making the router itself `msg.sender` to the pool: [4](#0-3) 

The real end-user identity is never surfaced to the pool or to any extension. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the economically relevant actor explicitly threaded through the call), not by `sender`: [5](#0-4) 

No equivalent end-user identity is threaded through the swap path, so the swap allowlist has no equivalent protection.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses faces an impossible choice: not allowlisting the router blocks all allowlisted users from using `MetricOmmSimpleRouter`; allowlisting the router disables the gate for every user. There is no configuration that enforces "only allowlisted users may swap via the router." Any non-allowlisted EOA can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the public router and execute swaps on the curated pool, directly breaking the curation invariant and enabling non-permitted swaps that drain pool liquidity at the pool's bid/ask spread. This constitutes a broken core pool access-control mechanism with direct fund-flow consequences.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard public swap entrypoint expected to be used by end users. Any pool admin who configures a `SwapAllowlistExtension`-gated pool and also wants router support will naturally allowlist the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any EOA can call the router. The precondition (router allowlisted) is the expected operational configuration, making this highly likely to be triggered in practice.

## Recommendation
Thread the real end-user identity through the swap path so extensions can gate it. The correct model is the `DepositAllowlistExtension` pattern: add a `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool. The pool passes this value to `_beforeSwap` alongside `sender`. `SwapAllowlistExtension.beforeSwap` should then check `allowedSwapper[pool][swapper]` instead of `allowedSwapper[pool][sender]`. Using `recipient` as a proxy is insufficient because multi-hop routes use `address(this)` as intermediate recipients. [6](#0-5) 

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, alice, true)
  admin calls setAllowedToSwap(pool, MetricOmmSimpleRouter, true)
  bob (non-allowlisted EOA) holds token0 and has approved the router

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
    pool: curated_pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: X,
    ...
  })

  Router stores payer=bob in transient storage, then calls:
    pool.swap(recipient=bob, zeroForOne=true, ...)
      msg.sender to pool = MetricOmmSimpleRouter

  Pool calls _beforeSwap(sender=MetricOmmSimpleRouter, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][MetricOmmSimpleRouter] → true
  Hook passes. Swap executes. Bob receives token1.

Result:
  bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist invariant is broken.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Admin allowlists alice and MetricOmmSimpleRouter.
  3. Bob (not allowlisted) calls router.exactInputSingle targeting the pool.
  4. Assert swap succeeds and bob receives output tokens.
  5. Assert bob is NOT in allowedSwapper[pool] mapping.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-106)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
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
