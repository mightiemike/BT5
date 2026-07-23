Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates whether the **router** is allowlisted rather than the **end user**. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users — a natural operational step — the allowlist is silently opened to every address on-chain, exposing LP liquidity to unrestricted trading.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the original `msg.sender` into `extensionData`:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

The router stores `msg.sender` only in transient storage for the payment callback — it is never forwarded to the pool or extension. When the pool executes, it sees `msg.sender = router` and passes `router` as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The `extensionData` bytes field is available in `beforeSwap`'s signature but the extension ignores it entirely, providing no path to recover the original caller. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter (the LP position owner explicitly passed by the caller), not `sender`, so it correctly identifies the actual depositor regardless of intermediary.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled bots) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted, the pool accepts arbitrary swaps from any address, exposing LP liquidity to unrestricted trading, potential price manipulation, and value extraction that the allowlist was designed to prevent. This constitutes broken core pool functionality with direct LP asset impact, satisfying the "Broken core pool functionality causing loss of funds" criterion.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point in the periphery. Any pool admin who deploys a `SwapAllowlistExtension` and then tries to enable router-mediated swaps for their allowlisted users will naturally call `setAllowedToSwap(pool, router, true)` — this is the only way to allow allowlisted users to use the official UI. That single routine configuration step triggers the bypass. No privileged access beyond the admin's own expected workflow is required; the attacker needs only to call the public router. The condition is therefore highly likely to be reached in production.

## Recommendation
The extension must resolve the actual end user, not the immediate pool caller. Two options:

**Option A — Pass original sender through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and verifies it. This is acceptable for a canonical periphery contract and requires no interface changes.

**Option B — Dedicated `originalSender` field in the hook interface**: Add an `originalSender` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a transient-storage slot set by the router before calling `pool.swap()`. The pool reads this slot and forwards it alongside `msg.sender`.

At minimum, `SwapAllowlistExtension` NatDoc and router documentation must warn that allowlisting the router grants unrestricted access to all users.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only permitted swapper
  allowedSwapper[pool][router] = true  // admin adds router so alice can use the UI

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  Execution trace:
    router.exactInputSingle()                          // msg.sender = bob
      → pool.swap(msg.sender=router, ...)              // pool sees router as caller
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ passes
        → swap executes, bob receives tokens

Result:
  bob successfully swaps on a pool he is not allowlisted for.
  The allowlist invariant is broken; LP funds are exposed to unrestricted trading.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension`.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. From `bob` (not allowlisted), call `router.exactInputSingle(...)`.
4. Assert the swap succeeds and `bob` receives output tokens — confirming the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
