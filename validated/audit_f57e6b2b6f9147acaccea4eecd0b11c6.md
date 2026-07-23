All four code references check out exactly as claimed. Let me confirm the full call chain:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with no originator argument — the router is `msg.sender` of that call. [1](#0-0) 

2. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router address, not the originating user. [4](#0-3) 

The same identity-loss applies to `exactOutputSingle`, `exactInput`, and `exactOutput` — none of them pass the originating user to `pool.swap()`. [5](#0-4) 

The finding is fully supported by production code.

---

Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` executes a swap, it is the direct caller of `pool.swap()`, so `sender` is always the router address. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently grants every user of the public router access to the pool, completely defeating the per-user allowlist.

## Finding Description
**Call chain:**
1. `MetricOmmSimpleRouter.exactInputSingle` (and `exactOutputSingle`, `exactInput`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The originating user's address is stored only in transient storage for the payment callback and is never forwarded to `pool.swap()`. The router is therefore `msg.sender` of `pool.swap()`.
2. `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`.
3. `ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to every configured extension via `_callExtensionsInOrder`.
4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router address.

**Resulting invariant break:**

| Scenario | Extension sees | Result |
|---|---|---|
| Router NOT allowlisted | `sender = router` → not in allowlist | All router swaps revert, even for allowlisted users |
| Router IS allowlisted | `sender = router` → passes | Every user bypasses the allowlist |

There is no existing guard that recovers the originating user's identity. The `extensionData` field passed by the router is caller-supplied and unauthenticated, so it cannot serve as a trusted originator signal without a protocol-level convention.

## Impact Explanation
Any unpermissioned user can trade on a curated pool (e.g., KYC-only, institutional-only) by routing through `MetricOmmSimpleRouter`. Non-allowlisted counterparties trade against LP capital on a pool configured to admit only vetted participants. For pools where the allowlist enforces regulatory or risk controls, this is a direct policy bypass with fund-level consequences: LP capital is exposed to counterparties the pool admin explicitly excluded. This constitutes broken core pool functionality (access control) causing potential loss of funds or regulatory liability for LPs.

## Likelihood Explanation
The precondition — allowlisting the router — is the natural, expected action for any pool admin who wants curated users to access the pool via the standard periphery. Nothing in the extension, pool, factory, or documentation warns that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`. The mistake is easy to make, hard to detect after the fact, and the router is a public permissionless contract requiring no special access to exploit.

## Recommendation
The extension must gate the economically relevant actor — the originating user — not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into the `extensionData` bytes it forwards to the pool; the extension decodes and checks that address. A convention (e.g., a fixed ABI prefix) must be established and enforced consistently across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

2. **Add an explicit `originator` field to the swap interface**: The pool passes both `msg.sender` (direct caller) and an optional `originator` (end user, supplied by the caller) to extensions; the allowlist checks `originator` when set. This requires an interface change but is more robust.

Either approach must be applied consistently across all four router entry points.

## Proof of Concept
```
Setup
─────
1. Pool admin deploys a curated pool with SwapAllowlistExtension.
2. Pool admin allowlists Alice (KYC'd):
       setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so Alice can use it:
       setAllowedToSwap(pool, router, true)

Attack
──────
4. Bob (non-KYC'd) calls:
       router.exactInputSingle({pool: curatedPool, ...})
   router calls pool.swap() → msg.sender = router
   _beforeSwap(sender = router, ...)
   extension checks allowedSwapper[pool][router] → true ✓
   Bob's swap executes on the curated pool.

Result
──────
Bob, who is not individually allowlisted, trades against LP capital
on a pool configured to admit only KYC'd counterparties.
The allowlist provides zero protection once the router is allowlisted.

Foundry test sketch
───────────────────
1. Deploy pool with SwapAllowlistExtension.
2. setAllowedToSwap(pool, alice, true).
3. setAllowedToSwap(pool, address(router), true).
4. vm.prank(bob); router.exactInputSingle({pool: pool, ...});
5. Assert swap succeeds (no NotAllowedToSwap revert).
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
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
