Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the immediate `pool.swap()` caller instead of the end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the actual end-user. If the pool admin allowlists the router to enable intended users to reach the pool via the standard router, every unprivileged user can bypass the allowlist by routing through the same public router. The allowlist is rendered completely ineffective for any pool that permits router access.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly with no user-identity forwarding:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← no encoded user identity
  );
```

The router's address becomes `sender` in the extension check. A pool admin who wants allowlisted users to reach the pool via the router must call `setAllowedToSwap(pool, router, true)`. Once that is done, `allowedSwapper[pool][router] == true` for every caller of the router, including completely unprivileged users. There is no mechanism to simultaneously allowlist the router and restrict which end-users may use it.

## Impact Explanation
This is an admin-boundary break. A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., approved market makers, KYC'd addresses, or protocol-internal actors) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted, the curation boundary is gone: unauthorized traders can execute swaps, causing adverse selection against LPs, violating the pool's intended access policy, and potentially draining value from a pool whose LP positions were sized under the assumption that only vetted counterparties would trade.

## Likelihood Explanation
The scenario is realistic and requires no privileged attacker capability. A pool admin who deploys a curated pool and wants their allowlisted users to access it via the standard router will naturally call `setAllowedToSwap(pool, router, true)`. Nothing in the interface, NatSpec, or documentation warns that this collapses the allowlist for all users. The bypass requires only that the pool admin takes this one intuitive configuration step; any user can then exploit it by calling the public router.

## Recommendation
The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention and the extension knowing which routers are trusted encoders.
2. **Document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly**: This is the minimal mitigation but breaks router UX for curated pools.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedMM` is allowlisted.
swapExt.setAllowedToSwap(pool, trustedMM, true);
// Pool admin also allowlists the router so trustedMM can use it:
swapExt.setAllowedToSwap(pool, address(router), true);

// Attack: attacker (not allowlisted) routes through the public router.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Extension sees sender = router → allowedSwapper[pool][router] = true → passes.
// Attacker swaps successfully despite not being on the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
