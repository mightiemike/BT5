Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of real swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the actual user. Any pool admin who allowlists the router (required for any allowlisted user to trade via the router) inadvertently grants every router caller — including explicitly excluded addresses — the ability to bypass the allowlist and swap on the restricted pool.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` verbatim.**

`_beforeSwap` encodes and dispatches `sender` as the first argument to every configured extension without modification: [2](#0-1) 

**Step 3 — `SwapAllowlistExtension` checks `sender`, not the real user.**

The extension evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument — the router address when routing through `MetricOmmSimpleRouter`: [3](#0-2) 

**Step 4 — The router is `msg.sender` of the pool call, not the user.**

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient callback context for payment, but calls `pool.swap(...)` directly — making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern applies to `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181).

**Why existing guards fail:** The `allowAllSwappers` flag is the only alternative path to bypass the per-address check, but it is a separate admin-controlled toggle. The per-address `allowedSwapper` mapping is the intended fine-grained gate. Once the router is allowlisted (a necessary precondition for any allowlisted user to trade via the router), `allowedSwapper[pool][router] == true` satisfies the check for every caller of the router, regardless of whether that caller is individually allowlisted.

## Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting which addresses may swap on a pool. When the router intermediates, the extension's identity check is permanently misdirected to the router address. Any address explicitly excluded from the allowlist can bypass the gate by calling any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). This breaks the core access-control invariant the extension is designed to enforce, permitting unauthorized swaps that drain pool liquidity at oracle-derived prices — a direct loss of LP principal. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break by an unprivileged path.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard user-facing swap interface. A pool admin who configures `SwapAllowlistExtension` and wants any allowlisted user to trade via the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no malicious setup, and no non-standard tokens. The attack requires only a single public router call and is repeatable indefinitely.

## Recommendation

The router must forward the real user's identity to the pool so extensions can gate on it. Two viable approaches:

1. **Transient-storage originator slot**: Before calling `pool.swap`, the router writes `msg.sender` into a well-known transient slot. The pool reads it and passes it as `sender` to extensions instead of its own `msg.sender`. The slot is cleared after the swap.

2. **Explicit originator parameter on `swap`**: Add an `originator` parameter to `IMetricOmmPoolActions.swap`. The pool passes `originator` (not `msg.sender`) to extensions. The router supplies `msg.sender`; direct callers supply `address(0)` (pool falls back to `msg.sender`).

Either approach ensures `SwapAllowlistExtension` always evaluates the economically relevant actor, not the router intermediary.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can trade via the router)
  pool admin does NOT call: swapExtension.setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls:
    router.exactInputSingle({
      pool:       pool,
      recipient:  attacker,
      zeroForOne: true,
      amountIn:   X,
      ...
    })

Execution trace:
  router.exactInputSingle
    → pool.swap(recipient=attacker, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, tokens transferred to attacker

Result:
  attacker — not on the allowlist — completes a swap on a restricted pool.
  LP principal is drained at oracle price by an unauthorized counterparty.
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
