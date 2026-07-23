Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender`, so `sender` arriving at the extension is the router address, not the originating user. This makes per-user swap access control unenforceable for all router-mediated swaps: either the router is allowlisted (every user on the network can bypass the individual gate) or it is not (allowlisted users cannot use the router at all).

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the router contract, so `sender` arriving at the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates two mutually exclusive failure modes: if the router is allowlisted, every user on the network can swap through it regardless of individual allowlist status; if the router is not allowlisted, individually allowlisted users cannot use the router at all.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the position owner), not `sender` (the operator/payer), because `addLiquidity` carries an explicit `owner` field representing the economic actor: [5](#0-4) 

The same `_beforeSwap` dispatch is used in `simulateSwapAndRevert`, confirming the bypass is present on both the live swap and simulation paths: [6](#0-5) 

## Impact Explanation
A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted arbitrageurs) cannot enforce that restriction for any user who routes through `MetricOmmSimpleRouter`. Any unprivileged address can call the public router and execute swaps against the pool. This allows unauthorized traders to extract value from LPs through adverse selection, front-running, or directional pressure that the allowlist was intended to prevent. The pool's LP token holders bear the resulting loss. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's access control configuration is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point. Any user who discovers the allowlist can trivially route around it by calling the router instead of the pool directly. No special privileges, flash loans, or contract deployment are required. The bypass is reachable by any EOA or contract on the network. The precondition — a pool configured with `SwapAllowlistExtension` and the router allowlisted — is the only operational configuration that permits router-mediated swaps at all, making the bypass automatic in any working deployment.

## Recommendation
Replace the `sender` identity check with the originating user's address. Two approaches:

1. **Pass the real user through the router**: Have `MetricOmmSimpleRouter` supply the originating `msg.sender` as a dedicated `swapper` field in `extensionData`, and have `SwapAllowlistExtension` decode and check that field instead of the raw `sender` parameter. This requires a coordinated interface change.

2. **Check `sender` against a router registry**: Maintain a registry of trusted routers in the extension; when `sender` is a known router, extract the real user from `extensionData` and check that address instead.

Either way, the extension must distinguish "the contract that called the pool" from "the economic actor initiating the swap."

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)  // must allowlist router for router swaps to work
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not individually allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  - Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - The per-user allowlist is completely bypassed
  - Any address can swap by routing through MetricOmmSimpleRouter
  - LPs are exposed to all counterparties the allowlist was meant to exclude
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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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
