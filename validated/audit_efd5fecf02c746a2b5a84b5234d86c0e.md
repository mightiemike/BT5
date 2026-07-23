Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap()` populates with `msg.sender` — the direct caller of the pool. When `MetricOmmSimpleRouter` intermediates the swap, `sender` resolves to the router's address, not the originating user. Any pool admin who allowlists the router (required for allowlisted users to use it) simultaneously grants every unpermissioned user the ability to bypass the per-user restriction by routing through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap(), not the original user
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter` calls `pool.swap()` directly in every entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) without encoding the originating user anywhere:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
);
```

From the pool's perspective `msg.sender` = router, so `sender` = router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool also calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` back to the router, confirming the router is the direct `msg.sender`. Existing guards are insufficient: there is no mechanism in the extension to recover the original user from `extensionData`, and no trusted-router registry exists.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` and `allowAllSwappers = false` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) is fully bypassed by any public user who routes through `MetricOmmSimpleRouter`. The attacker receives the same swap output as an allowlisted user, draining pool liquidity and violating the LP's curation intent. This is a direct admin-boundary break: the pool admin's access-control policy is circumvented by an unprivileged path available to any on-chain caller.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router — a natural and expected configuration for any pool that wants to support the standard periphery. `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who deploys `SwapAllowlistExtension` and also wants router support will inevitably create this bypass. The attacker needs no special privileges: a single public call to `router.exactInput(...)` or `router.exactInputSingle(...)` suffices, and the attack is repeatable on every block.

## Recommendation
The extension must gate on the **original user**, not the direct pool caller. The cleanest fix: the router appends `abi.encode(msg.sender)` to `extensionData` for each hop, and the extension maintains a factory-registered set of trusted routers. When `sender` is a known trusted router, the extension decodes and checks the embedded user address; otherwise it checks `sender` directly. This requires no changes to the pool core and preserves the allowlist semantics for both direct and routed swaps.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)
  - allowAllSwappers[pool] = false

Attack (bob is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...) → msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES
  5. Swap executes; bob receives tokens despite not being on the allowlist

Direct call (correctly blocked):
  1. bob calls pool.swap(...) directly → msg.sender = bob
  2. SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → REVERTS
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
