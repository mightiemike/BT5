Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` to the pool, so the extension checks the router's allowlist status rather than the actual user's. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently opens the pool to every user who routes through the same router, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value as the first argument to `IMetricOmmExtensions.beforeSwap` (L162-176). `SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly (L72-80), making the router `msg.sender` to the pool. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165) — all router entry points call `pool.swap()` with `msg.sender = router`. No existing guard in the extension or pool checks the originating user identity when a forwarder is involved.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to curated addresses.
2. Admin allowlists specific users: `setAllowedToSwap(pool, userA, true)`.
3. Admin allowlists the router so curated users can use standard periphery: `setAllowedToSwap(pool, router, true)`.
4. Non-allowlisted `userB` calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(recipient=userB, ...)` — `msg.sender` to pool is the router.
6. Extension checks `allowedSwapper[pool][router] == true` → passes.
7. `userB`'s swap executes against the curated pool's LP liquidity.

## Impact Explanation
LP providers who deposited under the assumption that only vetted counterparties could trade against their positions are exposed to unrestricted adverse selection, fee extraction, and potential oracle-anchored price manipulation by arbitrary actors. This constitutes a direct loss of LP principal and owed fees above Sherlock thresholds, matching the "broken core pool functionality causing loss of funds" and "admin-boundary break" allowed impact categories.

## Likelihood Explanation
Likelihood is high. Allowlisting the router is the natural and expected administrative action for any pool that wants its curated users to access the standard periphery. The admin has no on-chain signal that doing so opens the pool to everyone. The bypass requires no special privilege, no malicious setup, and no non-standard token — any user with a standard ERC-20 balance can trigger it in a single transaction, repeatably.

## Recommendation
The extension must gate on the economically relevant actor, not the intermediary. Preferred options:

1. **Forward real user identity via `extensionData`**: Require the router to encode `msg.sender` into `extensionData` and have the extension decode and check that value when `sender` is a known forwarder.
2. **Gate on `recipient` instead of `sender`**: The `recipient` argument is already correctly forwarded by the router as `params.recipient` (user-controlled), and the admin can allowlist recipient addresses directly.
3. **Reject router-mediated swaps at the extension level**: Check `sender == tx.origin` (direct EOA only) or maintain a separate registry of trusted forwarders that must themselves enforce per-user checks.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, userA, true)
  admin: setAllowedToSwap(pool, router, true)   ← enables router for userA

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: userB, ...})

  router calls pool.swap(recipient=userB, ...)
    msg.sender to pool = router

  pool calls extension.beforeSwap(sender=router, ...)
    extension checks: allowedSwapper[pool][router] == true  → passes

  userB's swap executes; LP funds consumed by non-allowlisted actor.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)`, then call `router.exactInputSingle` from an address not in the allowlist and assert the swap succeeds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
