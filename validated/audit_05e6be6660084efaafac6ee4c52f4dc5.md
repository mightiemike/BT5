Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of End User, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` — the router contract — not the originating user. When a pool admin allowlists the router to permit router-mediated swaps for their curated users, every unpermissioned user can bypass the allowlist by calling through the same public `MetricOmmSimpleRouter`. The extension never evaluates the actual end user's address.

## Finding Description

**Root cause:** In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- pool's msg.sender = router when routed
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` (L149-177) encodes this value and dispatches it to the extension as `sender`. The extension then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**Exploit path:**

1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to KYC'd users.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is allowlisted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required for Alice to use `MetricOmmSimpleRouter.exactInputSingle`.
4. Charlie (never allowlisted) calls `router.exactInputSingle({pool: pool, recipient: charlie, ...})`.
5. Router calls `pool.swap(charlie, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(router, charlie, ...)`.
7. Extension checks `allowedSwapper[pool][router] == true` → passes.
8. Charlie's swap executes. `allowedSwapper[pool][alice]` is never consulted.

The same misbinding applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` since the pool's direct caller is always the router in all paths.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional partners) is completely open to any user routing through `MetricOmmSimpleRouter` once the router is allowlisted. The allowlist policy is nullified the moment the pool admin enables router support. Unauthorized users can execute swaps against the pool, extracting value at oracle prices and accruing fees that were intended only for the curated participant set. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path bypasses the pool admin's access control.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point. Any pool admin who wants allowlisted users to have normal UX (deadline, slippage protection, multi-hop) must allowlist the router — there is no alternative. The bypass requires zero special knowledge: any user calls the public router with the target pool address. The condition is met by any pool that has both `SwapAllowlistExtension` configured and the router allowlisted, which is the expected production configuration.

## Recommendation

Pass the originating end user's address through the swap path so the extension can gate on the economically relevant actor. One approach: add a `swapper` field to `extensionData` that the router populates with its `msg.sender` before calling the pool, and have the extension decode and verify it against the allowlist. A cleaner approach is to have the pool accept an explicit `swapper` parameter (separate from `msg.sender`) that the router fills with its own `msg.sender`, and pass that to extensions as the gated identity. Either way, `SwapAllowlistExtension.beforeSwap` must receive the address of the human/contract that initiated the trade, not the address of the intermediary router.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: setAllowedToSwap(pool, alice, true)
  pool admin calls: setAllowedToSwap(pool, router, true)  // required for Alice to use router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, amountIn: X, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(charlie, ...)          // pool.msg.sender = router
    → _beforeSwap(router, charlie, ...)
    → extension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓
    → swap executes, charlie receives output tokens

Result: allowedSwapper[pool][alice] is never consulted.
        The router's allowlist entry is shared by all callers.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and `router`, call `exactInputSingle` from `charlie`, assert swap succeeds and `charlie` receives output tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
