Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, the router becomes `msg.sender` of the pool call, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][end-user]`. A pool admin who allowlists the router to enable allowlisted users to reach the pool through the canonical periphery path simultaneously grants every unprivileged user the same access.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` evaluates the allowlist against that `sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

There is no mechanism in `SwapAllowlistExtension` to distinguish the end-user from the intermediary router. The moment `setAllowedToSwap(pool, router, true)` is called, `allowedSwapper[pool][router]` evaluates to `true` for every caller of the router, regardless of who they are. The `DepositAllowlistExtension` avoids this problem because it gates `owner` (the position recipient), not `sender` (the direct caller): [6](#0-5) 

## Impact Explanation
Any user who calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) against a curated pool where the router has been allowlisted can execute swaps that the pool admin intended to restrict to a specific set of addresses. The unauthorized trader can drain LP value from the pool at oracle-derived prices, causing direct loss of LP principal. This breaks the core invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it. Severity: High — direct loss of LP principal, broken core pool access-control functionality.

## Likelihood Explanation
The router is the canonical periphery swap path. A pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)` — there is no other mechanism. The bypass is therefore a natural consequence of normal, expected admin configuration, reachable by any unprivileged user with no special setup, no privileged role, and no front-running required.

## Recommendation
The extension must gate the economically relevant actor — the end-user — not the intermediary contract. Two sound approaches:

1. **Caller-forwarded identity**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it, combined with a check that `msg.sender` (the pool's caller) is a trusted router registered in the factory.
2. **Pool-level sender enrichment**: Extend the pool's `swap` signature or the extension callback to carry both the direct caller and an optional forwarded originator, letting the extension choose which identity to gate.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is a KYC-approved user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use `MetricOmmSimpleRouter`.
4. Non-allowlisted Bob calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` of the pool call is the router.
6. The pool calls `extension.beforeSwap(router, ...)` — `sender = router`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes at oracle price, extracting value from LP positions he was never authorized to trade against.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
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
