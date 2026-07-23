Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` as seen by the pool. When users route through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than the actual end user. Any unprivileged user can bypass the curated allowlist entirely by routing through the router if the router is allowlisted.

## Finding Description
The complete call chain is confirmed in production code:

1. `MetricOmmSimpleRouter.exactInputSingle` stores the actual `msg.sender` only in transient storage for payment callback purposes via `_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`, and passes `params.extensionData` (caller-supplied, not encoding the real user) directly to the pool. [1](#0-0) 

2. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding the **router address** as `sender`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes and dispatches `beforeSwap(sender=router, ...)` to the configured extension. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`, never seeing the actual end user's address. [4](#0-3) 

The exact wrong value is the identity checked by the allowlist: `allowedSwapper[pool][router]` is evaluated when `allowedSwapper[pool][actual_user]` is the correct invariant. No existing guard recovers the real caller — the router does not encode `msg.sender` into `extensionData`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position owner explicitly supplied by the caller), which is the economically relevant identity for deposits and is not subject to this intermediary-address substitution. [5](#0-4) 

## Impact Explanation
The swap allowlist is the primary access-control mechanism for curated pools. A complete bypass means any unprivileged user can trade on a pool explicitly configured to restrict swappers. This breaks the core pool functionality the admin configured and exposes LP funds to trades from counterparties the pool was designed to exclude — a direct broken core pool functionality impact meeting Sherlock High severity thresholds.

## Likelihood Explanation
The router is the standard, documented periphery path for swaps. Any pool admin who configures a swap allowlist and also expects allowlisted users to use the router will naturally allowlist the router address, directly triggering the bypass. The attack requires no special setup beyond the normal intended usage of both components together, is repeatable by any unprivileged user, and requires no privileged access.

## Recommendation
The `SwapAllowlistExtension` must gate on the actual end user, not the intermediary router. Two viable approaches:

1. **Router forwards caller in `extensionData`:** The router encodes `msg.sender` (the actual user) into the `extensionData` it passes to the pool. The extension decodes and checks that address when `sender` is a known router.
2. **Trusted router registry in the extension:** The extension maintains a registry of trusted routers; when `sender` is a router, it reads the actual user from a standardized field in `extensionData`.

The invariant must be: the identity checked by the allowlist is the same actor who economically initiates and benefits from the swap.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)   // Alice is allowlisted
  admin: setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: ..., ...})

  router → pool.swap(recipient=bob, ...)
    msg.sender to pool = router

  pool → _beforeSwap(sender=router, ...)
  extension: allowedSwapper[pool][router] == true  ✓ passes

  Bob swaps successfully despite not being on the allowlist.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist Alice and the router, call `router.exactInputSingle` from Bob's address, assert the swap succeeds and Bob receives output tokens despite `allowedSwapper[pool][bob] == false`.

### Citations

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
