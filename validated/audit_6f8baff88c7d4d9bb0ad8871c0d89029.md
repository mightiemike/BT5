Audit Report

## Title
`SwapAllowlistExtension` checks the immediate `pool.swap()` caller (router) instead of the end-user, allowing any user to bypass the per-swapper allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the router is allowlisted (a prerequisite for any router-mediated swap to succeed), every unpermissioned user on the network can bypass the per-swapper allowlist by routing through the router.

## Finding Description

**Step 1 — Pool captures `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` directly as the first argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:** [2](#0-1) 

**Step 3 — Extension checks `sender` against the allowlist:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 4 — Router calls `pool.swap()` directly, substituting itself as `sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` with no mechanism to forward the original `msg.sender` (the end-user): [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Why existing guards fail:**

The extension has no mechanism to recover the true initiator. The `recipient` field (second argument to `beforeSwap`) is the output recipient, not the trade initiator, and can be set to any arbitrary address. The `extensionData` field is user-supplied and not authenticated. There is no on-chain link between the router call and the original `msg.sender`.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — an explicit parameter passed by the caller representing the LP position owner — rather than `sender`. This works because `addLiquidity` has a dedicated `owner` parameter that is semantically the gated party. The swap path has no equivalent. [6](#0-5) 

## Impact Explanation

`SwapAllowlistExtension` is the production access-control gate for pools that restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional desks). The broken invariant is `allowedSwapper[pool][user]` — the extension evaluates `allowedSwapper[pool][router]` instead. If the router is allowlisted, every unpermissioned address can execute swaps against a restricted pool, draining liquidity at oracle prices. This constitutes a broken core pool functionality and admin-boundary break with direct fund-impacting consequences.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entry point in the periphery. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The router is a standard deployed contract, so routing through it is a normal user action. The bypass is repeatable and requires only a single transaction.

## Recommendation

The extension must check the original end-user identity, not the immediate caller of `pool.swap()`. The cleanest fix matching the stated design intent ("gates `swap` by swapper address") is to have the router encode `msg.sender` (the end-user) into `extensionData` before calling `pool.swap()`, and have the extension decode and authenticate that value. Alternatively, move the per-user check into the router itself and only allowlist the router at the extension level, centralizing trust in the router.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)
    // router must be allowlisted for any router-mediated swap to succeed
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=attacker, ...)
    // msg.sender inside pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension evaluates allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens

Result:
  - attacker, who was never individually allowlisted, successfully swaps
    on a pool whose admin explicitly restricted access
  - The per-user allowlist invariant is completely bypassed
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
