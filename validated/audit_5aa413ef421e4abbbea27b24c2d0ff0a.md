Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of End User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the allowlist gates on the router address rather than the actual trader. Any pool that allowlists the router to permit legitimate users simultaneously opens itself to every unprivileged caller, rendering the allowlist ineffective.

## Finding Description
In `SwapAllowlistExtension.beforeSwap` (L37), `msg.sender` is the pool and `sender` is the first argument forwarded by the pool — which is `msg.sender` at the time `pool.swap()` was called:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

In `MetricOmmPool.swap` (L230–231), that argument is always `msg.sender` — the immediate caller of the pool:

```solidity
_beforeSwap(msg.sender, recipient, ...);
```

`ExtensionCalling._beforeSwap` (L149–177) faithfully forwards this value to the extension without modification.

In `MetricOmmSimpleRouter.exactInputSingle` (L72–80), the router calls `pool.swap(...)` directly, making `msg.sender` inside the pool the **router address**, not the end user. The same applies to `exactInput` (L104–112), `exactOutputSingle` (L136–137), and the recursive `_exactOutputIterateCallback` path (L220–228) where intermediate hops are also called from the router.

The pool admin faces an inescapable dilemma: not allowlisting the router blocks all legitimate users from using the supported periphery; allowlisting the router opens the pool to every unprivileged caller. No configuration simultaneously permits allowlisted users through the router while blocking non-allowlisted users.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for KYC, whitelist, or private-market access is fully bypassable by any unprivileged user who calls `MetricOmmSimpleRouter`. Unauthorized swaps execute at oracle prices against pool liquidity, directly harming LPs who deposited under the assumption that only vetted counterparties could trade. This constitutes broken core pool functionality causing direct loss of LP assets and falls under the admin-boundary break category (an unprivileged path bypasses a pool admin-configured access control).

## Likelihood Explanation
`SwapAllowlistExtension` is a production extension. Any pool deploying it that also wants users to access it through the standard router — a normal operational expectation — will allowlist the router. The bypass is then unconditionally reachable by any public caller with no preconditions beyond knowing the router address. The attack is repeatable and requires no special privilege.

## Recommendation
The extension must gate on the economic actor (the end user), not the immediate pool caller. The simplest sound fix: the router always appends `abi.encode(msg.sender)` to `extensionData` before calling the pool; the extension decodes the real swapper from `extensionData` when `sender` is a recognized router address, and falls back to checking `sender` directly for non-router callers. Alternatively, the router encodes the originating user into `extensionData` by convention, and the extension always decodes and checks that address.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only intended swapper
  allowedSwapper[pool][router] = true  // admin allowlists router so alice can use periphery

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls (MetricOmmSimpleRouter.sol L72-80):
    pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    // msg.sender inside pool = router address

  pool calls (MetricOmmPool.sol L230-231):
    _beforeSwap(msg.sender /*= router*/, recipient, ...)

  extension checks (SwapAllowlistExtension.sol L37):
    allowedSwapper[pool][router] == true  → PASSES

  bob's swap executes on the allowlisted pool despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
