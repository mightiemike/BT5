Audit Report

## Title
SwapAllowlistExtension checks router address instead of end-user identity, allowing allowlist bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address rather than the originating user. A pool admin who allowlists the router to support standard periphery UX inadvertently grants unrestricted swap access to every user on the network.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks: [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

The router does not encode the originating `msg.sender` anywhere in the call to `pool.swap()`. Consequently `sender = address(router)` reaches the extension. The allowlist check passes for any caller as long as the router is allowlisted, regardless of who initiated the transaction.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` (adder contract) argument entirely and checks `owner` — the economic beneficiary — which is immune to this intermediary-address substitution: [5](#0-4) 

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified or otherwise approved addresses loses that restriction entirely for any user who calls `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` once the router is allowlisted. Unauthorized users can trade against the pool's liquidity at oracle-anchored prices, extracting value from LP positions provisioned under the assumption that only vetted counterparties would trade. This constitutes a direct loss of the access-control guarantee that LP providers relied upon when depositing funds, meeting the broken core pool functionality and admin-boundary bypass impact criteria.

## Likelihood Explanation
The trigger condition — the router being allowlisted — is the natural and expected configuration for any pool that wants to support the standard periphery UX. `MetricOmmSimpleRouter` is the primary public swap interface. A pool admin who deploys `SwapAllowlistExtension` and then adds the router to the allowlist (to let their approved users trade via the router) will unknowingly open the pool to all users. No special privilege or malicious setup is required from the attacker; calling `router.exactInputSingle` with a valid pool address is sufficient.

## Recommendation
The extension must receive and verify the originating user identity, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Pass the end-user through `extensionData`**: require the router to encode `msg.sender` in `extensionData` and have the extension decode and verify it against the allowlist. This requires a coordinated change to the router and the extension.
2. **Trusted router concept with transient storage**: when `sender == trustedRouter`, read the actual payer from transient storage written by the router before calling `pool.swap()`, and verify that address against the allowlist.

The simplest safe interim fix is to document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps by reverting if `sender` is a known router address, forcing direct pool interaction for allowlisted pools.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true           // alice is KYC-approved
  allowedSwapper[pool][address(router)] = true // admin adds router so alice can use it

Attack:
  bob (not KYC-approved) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, tokenOut: token1, ...})

Execution path:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)           // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router)
          → allowedSwapper[pool][router] == true  ✓ PASSES
    → swap executes, bob receives token1

Result: bob bypasses the allowlist and trades on a restricted pool.
```

A Foundry integration test can confirm this by deploying a pool with `SwapAllowlistExtension`, allowlisting only `alice` and `address(router)`, then calling `router.exactInputSingle` from an address `bob` that is not in the allowlist and asserting the swap succeeds.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
