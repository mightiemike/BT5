Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool call, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps for approved users simultaneously opens the gate to every user on the network.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router contract the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput` (intermediate hops use `address(this)` as payer): [5](#0-4) 

And to `exactOutputSingle` and the recursive `_exactOutputIterateCallback` hops: [6](#0-5) [7](#0-6) 

The result is an irreconcilable mismatch: if the router is not allowlisted, all router users are blocked (including legitimately approved ones); if the router is allowlisted (the natural operational step), every user bypasses the gate. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner`, which is a separate user-controlled argument: [8](#0-7) 

No equivalent user-identity argument exists on the swap interface (`beforeSwap` receives only `sender` and `recipient`), so the extension has no way to recover the real initiating user when the router is the direct caller. [9](#0-8) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle-quoted prices against a pool whose admin intended to reserve access for approved counterparties only. This is a direct, high-severity allowlist bypass with fund-flow impact: non-approved users can drain pool liquidity at will, contradicting the pool admin's access-control intent and constituting a broken core pool functionality / unauthorized fund access finding.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical production swap entry point. The only precondition is that the pool admin has allowlisted the router address — the expected and necessary operational step for any curated pool that wants approved users to benefit from multi-hop routing, slippage protection, and deadline enforcement. Once that step is taken, the bypass requires no special privilege: any EOA calls `exactInputSingle` on the router. The condition is self-inflicted by normal pool administration and is therefore highly likely to be triggered in production.

## Recommendation
The swap allowlist must gate the economically relevant actor — the user who initiates and pays for the swap — not the intermediate relay contract.

1. **Router-side fix**: Have the router encode the original `msg.sender` inside `extensionData` (authenticated via a trusted-router registry or signed payload). `SwapAllowlistExtension.beforeSwap` decodes and checks that field instead of `sender` when the caller is a known router.
2. **Extension-side fix**: Redesign `SwapAllowlistExtension` to check `recipient` when `sender` is a registered trusted router, or document and enforce that pools using this extension must be called directly (no router).

The deposit allowlist's pattern — checking `owner` rather than `sender` — is the correct model; the swap allowlist needs an equivalent user-identity field that survives router indirection.

## Proof of Concept
```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap extension.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // alice is approved
3. Pool admin: setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool:       pool,
           recipient:  bob,
           zeroForOne: true,
           amountIn:   X,
           ...
       })

5. Router executes: pool.swap(bob, true, X, ...)
   → msg.sender of pool.swap = router

6. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
   → checks allowedSwapper[pool][router] → TRUE ✓

7. Swap executes. Bob receives tokens from a pool he was never approved to trade in.

Verification
────────────
Direct call by bob:
   pool.swap(bob, true, X, ...)
   → sender = bob → allowedSwapper[pool][bob] → FALSE → reverts NotAllowedToSwap ✓

Router call by bob:
   router.exactInputSingle(...) → succeeds (bypass confirmed)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
