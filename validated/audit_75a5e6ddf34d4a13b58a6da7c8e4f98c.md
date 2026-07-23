Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Enabling Full Per-User Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. If a pool admin allowlists the router to enable router-based swaps on a curated pool, every user — including those not individually allowlisted — passes the check, completely nullifying the per-user restriction.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)`: [4](#0-3) 

This makes `msg.sender` of `pool.swap()` the router address, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the first parameter (the `LiquidityAdder` contract address) and checks `owner` — the actual position owner passed explicitly: [5](#0-4) 

The `beforeSwap` interface has no dedicated owner/user slot — only `sender` (direct caller) and `recipient` (output destination) — so there is no equivalent field to check for the actual end user: [6](#0-5) 

The `allowedSwapper` mapping is keyed by `[pool][sender]`, so allowlisting the router grants access to all callers of the router: [7](#0-6) 

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties) faces a complete allowlist bypass when the router is involved. If the admin allowlists `MetricOmmSimpleRouter` to enable router-based swaps, every user — including those not individually allowlisted — passes the `allowedSwapper[pool][router]` check. Non-allowlisted users can trade against a pool intended to be private, extracting value from LP positions priced for a restricted counterparty set. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break reachable by an unprivileged path.

## Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery contract explicitly designed for curated pools.
- `MetricOmmSimpleRouter` is the canonical swap entry point for EOA users.
- A pool admin enabling router-based swaps on a curated pool will naturally allowlist the router — the exact action that triggers the bypass.
- No privileged attacker is required; any EOA can call the router.
- The bypass is repeatable and requires no special conditions beyond the admin's expected configuration.

## Recommendation

The `beforeSwap` hook must gate on the actual end user, not the intermediary. Two viable approaches:

1. **Decode user identity from `extensionData`:** Require the router to embed the actual user address in `extensionData`, and have `SwapAllowlistExtension` decode and check it. This mirrors how `addLiquidity` carries `owner` explicitly via `positionOwner`.

2. **Check `recipient` as a proxy:** If the protocol guarantees `recipient == user` for all router paths (verify this invariant holds), gate on `recipient` instead of `sender`. Note this does not hold for multi-hop `exactInput` intermediate hops where `recipient` is `address(this)` (the router).

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is individually allowed
  allowedSwapper[pool][bob]   = false         // bob is NOT allowed
  allowedSwapper[pool][router] = true         // admin enables router swaps

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=bob, ...)          [MetricOmmSimpleRouter.sol L72-80]
    → pool calls _beforeSwap(sender=router, ...)          [MetricOmmPool.sol L230-240]
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  [SwapAllowlistExtension.sol L37]
    → check passes
    → bob's swap executes against the curated pool
    → bob bypasses the individual allowlist entirely
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
