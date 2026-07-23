Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender`, not the actual end user — any user can bypass a per-user swap allowlist by routing through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap`. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the allowlist checks the router's address rather than the actual end user. If the router is allowlisted — the natural admin action to enable router-based trading — every user on-chain can bypass the per-user allowlist and swap in a pool intended to be restricted to specific addresses.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router directly calls `pool.swap(...)`, making the router `msg.sender` to the pool: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router (the expected operational action to enable router-based trading), every user — including those not individually allowlisted — can swap freely through the router. The per-user allowlist is completely defeated.

`DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the explicit position owner), not the `msg.sender` of `addLiquidity`: [5](#0-4) 

The swap path has no equivalent forwarding of the true caller identity.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The bypassing user executes swaps against the pool's liquidity, extracting tokens from LPs who deposited under the assumption that only allowlisted parties could trade. This is a direct loss of LP principal through unauthorized swap execution. The exact corrupted value is the `allowedSwapper[pool][sender]` extension decision — it evaluates `true` for the router when it should evaluate against the actual end user, causing the hook to pass when it should revert.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who enables `SwapAllowlistExtension` and also wants users to trade via the router must allowlist the router — at which point the bypass is immediately active for all users. The trigger requires no special privileges: any EOA can call the router. The only precondition is that the pool admin has allowlisted the router, which is the expected operational action.

## Recommendation

The pool must forward the true originating user identity to extensions, not just `msg.sender`. Two approaches:

1. **Router passes the real sender via `extensionData`**: The router encodes the actual user address into `extensionData`, and `SwapAllowlistExtension` decodes and checks it. This requires a convention between router and extension.

2. **Pool exposes a `senderOverride` parameter**: Add an optional `senderForExtensions` argument to `pool.swap` that trusted routers can populate with the real user address. The pool validates that if `senderForExtensions != address(0)`, `msg.sender` is a factory-registered trusted router before forwarding it to extensions.

Either approach must ensure the override path cannot be spoofed by an untrusted caller.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (allowlisting the router so users can trade via it)
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. Attacker (not individually allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=attacker, ...)
       → pool passes msg.sender (= router) as `sender` to _beforeSwap
  3. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router] == true  ✓ (router is allowlisted)
       → hook passes, swap executes
  4. Attacker receives output tokens; pool LPs bear the trade

Result: Attacker swapped in a pool intended to be restricted to specific users.
        The per-user allowlist provided zero protection.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
