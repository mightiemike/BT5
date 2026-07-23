Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `sender` — the immediate `msg.sender` of `pool.swap()` — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. A pool admin who allowlists the router to enable approved users to trade via the standard periphery inadvertently grants every router caller access to the pool, defeating the per-user allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling.sol` forwards this value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks this `sender` value against the allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself the `msg.sender` of that call: [4](#0-3) 

Therefore, `sender` seen by the extension is always the router address, never the end user. The extension has no mechanism to recover the true initiating user — it does not inspect `extensionData` or `recipient`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the economically relevant actor): [5](#0-4) 

This asymmetry confirms the swap allowlist was intended to gate the end user but is implemented to gate the immediate caller.

## Impact Explanation
A curated pool (KYC-gated, institutional-only, or restricted-strategy) relying on `SwapAllowlistExtension` to control who can trade is fully bypassed once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactOutputSingle` and execute swaps on the pool. LPs are exposed to adverse selection from uninvited counterparties, violation of curation policy, and direct LP principal loss if the pool's spread/fee parameters were calibrated for a known, trusted counterparty set. This matches the allowed impact gate: *admin-boundary break where an access control check is bypassed by an unprivileged path*.

## Likelihood Explanation
The bypass is triggered by a routine, expected administrative action. Any pool admin who deploys a pool with `SwapAllowlistExtension` to restrict trading and then allowlists the router so approved users can trade via the standard periphery will unknowingly open the pool to all router users. No attacker privilege is required beyond calling the public router. The condition is the natural and documented usage pattern for periphery-integrated pools.

## Recommendation
Gate on the true initiating user rather than the immediate caller. Two complementary approaches:

1. **Pass the original caller through `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the end user) into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a convention between router and extension.
2. **Check `recipient` as a proxy** (partial): For direct-user flows, `recipient` often equals the user. This is imperfect but better than checking the router address.

The deposit allowlist's pattern of checking `owner` (the economically relevant actor) should be the model for the swap allowlist.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists user A:  E.setAllowedToSwap(P, userA, true)
  admin allowlists router R: E.setAllowedToSwap(P, router, true)
    (necessary so userA can trade via the router)

Attack (by userC, not allowlisted):
  userC calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  Router calls P.swap(recipient=userC, ...)          // router is msg.sender
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks: allowedSwapper[P][router] == true  → passes
  Swap executes for userC despite userC not being on the allowlist.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
