Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router contract is `msg.sender` to the pool, so the allowlist gates the router address rather than the actual end user. This makes the allowlist either trivially bypassable (if the router is allowlisted) or silently broken for all legitimate router users (if it is not).

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract calling the extension hook) and `sender` is the first argument forwarded by the pool — the value of `msg.sender` inside `pool.swap()` at the time the hook fires. [1](#0-0) 

`ExtensionCalling._beforeSwap` passes the pool's `sender` parameter directly as the first argument to every registered extension: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router contract the `msg.sender` inside the pool: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_end_user]`. Two exploitable outcomes follow:

1. **Bypass**: If the pool admin allowlists the router address (a natural mistake when trying to permit router-mediated swaps), every user — including those the admin intended to block — can swap freely by routing through `MetricOmmSimpleRouter`.
2. **Broken gate**: If the router is not allowlisted, every allowlisted user is silently blocked from using the standard periphery path, even though they hold explicit permission.

`DepositAllowlistExtension.beforeAddLiquidity` does not share this flaw because it checks the explicit `owner` parameter, which the liquidity adder sets to the real user's address: [5](#0-4) 

Swaps have no equivalent explicit-user parameter; the only identity the pool forwards is `msg.sender`.

## Impact Explanation

**Direct loss of user principal / broken core pool functionality.**

- **Bypass path**: A non-allowlisted user calls `MetricOmmSimpleRouter.exactInput` targeting a curated pool whose admin has allowlisted the router. The extension passes, the swap executes, and LP funds are consumed by an actor the pool was designed to exclude. On a pool with a narrow allowlist (e.g., a private market-making pool), this directly drains LP principal.
- **Broken path**: Allowlisted users cannot use the standard router at all, making the pool's primary swap interface unusable for its intended participants. This constitutes broken core pool functionality.

Both outcomes meet Sherlock Medium/High thresholds: the bypass enables unauthorized extraction of LP assets; the broken gate renders the pool's swap flow unusable for legitimate users.

## Likelihood Explanation

**High.** The trigger requires no privilege. Any user can call `MetricOmmSimpleRouter` — it is a public, permissionless periphery contract. The bypass is reachable on every pool that uses `SwapAllowlistExtension` and has the router in its allowlist. The broken-gate variant affects every allowlisted user who uses the standard router path, which is the documented primary swap entrypoint.

## Recommendation

The extension must identify the economic actor, not the immediate caller. Two approaches:

1. **Pass the real user via `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires a trusted router convention and is fragile if other routers are added.

2. **Add an explicit `swapper` field to the hook signature**: Redesign the `beforeSwap` hook to include a dedicated `swapper` address that the pool sets to `tx.origin` or to a value the caller explicitly declares and the pool validates. The cleanest fix is for the pool to pass the original `msg.sender` of the top-level call, not the immediate caller, or to require the router to declare the real user in a verified field.

The intended check should be:

```solidity
if (!allowAllSwappers[pool] && !allowedSwapper[pool][real_end_user]) {
    revert NotAllowedToSwap();
}
```

where `real_end_user` is the address the pool admin actually intends to gate.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users only,
     or mistakenly thinking this is how to enable the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. Attacker (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
     targeting the curated pool.
  2. Router calls pool.swap(attacker_as_recipient, ...).
     msg.sender inside pool = router_address.
  3. Pool calls ExtensionCalling._beforeSwap(router_address, ...) which
     calls SwapAllowlistExtension.beforeSwap(router_address, ...).
  4. Extension checks: allowedSwapper[pool][router_address] == true  ✓
  5. Swap executes. Attacker receives tokens from LP reserves.

Result:
  Non-allowlisted attacker successfully swaps on a curated pool,
  extracting LP principal that the allowlist was designed to protect.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
