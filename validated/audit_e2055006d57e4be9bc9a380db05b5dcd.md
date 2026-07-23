Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of actual end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When any user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the actual end-user. If the pool admin allowlists the router (required for any router-mediated swap to function), every user — including non-allowlisted ones — can bypass the per-user allowlist by calling the router, enabling unauthorized trades against LP funds.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, i.e. `allowedSwapper[pool][sender]`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the pool sees `msg.sender = router`, so `sender` forwarded to the extension is the router address, not the actual caller: [3](#0-2) 

The same applies to `exactInput`, where intermediate hops use `address(this)` (the router) as the effective sender: [4](#0-3) 

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to KYC-approved addresses.
2. Admin allowlists approved users: `allowedSwapper[pool][user1] = true`.
3. Admin allowlists the router so approved users can interact via the standard UI: `allowedSwapper[pool][router] = true`.
4. Non-approved attacker calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully trades against LP funds despite not being allowlisted.

The wrong value is `allowedSwapper[pool][router]` — a single boolean that covers every user routing through the contract, collapsing the per-user gate into a per-router gate.

## Impact Explanation
The `SwapAllowlistExtension` is the only on-chain mechanism for a pool admin to restrict which addresses can trade against LP funds. Once the router is allowlisted (a necessary step for any router-mediated swap to function), the guard is rendered completely ineffective: any address can trade against LP funds by routing through the public router. This constitutes a direct loss of LP principal — pool token balances are reduced by trades the allowlist was specifically configured to prevent. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation
The scenario is highly likely in practice. Pool admins who deploy a `SwapAllowlistExtension` will naturally want their approved users to interact via the standard periphery UI (the router). Allowlisting the router is the only way to enable that. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the public router. The precondition (router being allowlisted) is an expected operational state, not an edge case.

## Recommendation
The extension must gate the actual end-user, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through the router**: The router should encode `msg.sender` (the real user) into `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and verify it when `sender` is a known router. This requires a coordinated change between the router and the extension.

2. **Alternatively, maintain a router registry**: The extension could maintain a set of trusted router addresses and, when `sender` is a known router, decode the real user from `extensionData` rather than trusting `sender` directly.

The cleanest fix is option 1: the router appends the real user's address to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

## Proof of Concept
```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists alice (approved user) and the router.
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// Bob is NOT allowlisted.
// Bob calls the router — pool sees msg.sender = router, check passes.
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(token0),
    tokenOut:         address(token1),
    zeroForOne:       true,
    amountIn:         1_000,
    amountOutMinimum: 0,
    recipient:        bob,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// Bob receives token1 from LP funds despite not being allowlisted.
assertGt(token1.balanceOf(bob), 0);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
