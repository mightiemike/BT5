Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool binds to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router — a natural action to enable periphery access — inadvertently grants every non-allowlisted user the ability to bypass the curated pool's swap gate.

## Finding Description

**Root cause — wrong actor binding:**

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct namespace) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` of the `swap()` call as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes this value directly as the first argument to the extension call: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all `exact*` variants) calls `pool.swap()` directly, making the router contract `msg.sender` inside the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` — an explicit beneficiary parameter — not `sender` (the direct caller). This correctly gates the economic actor regardless of who calls `addLiquidity`. [6](#0-5) 

The swap path has no equivalent explicit beneficiary parameter; only `sender` (the direct caller of `pool.swap()`) is available to the extension.

**Exploit path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only specific addresses (e.g., KYC'd traders): `allowedSwapper[pool][kyc_user] = true`.
2. Pool admin also allowlists `MetricOmmSimpleRouter`: `allowedSwapper[pool][router] = true`, believing the router is a neutral intermediary.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(...)` → pool passes `msg.sender = router` as `sender` → extension evaluates `allowedSwapper[pool][router] == true` → swap proceeds.
5. The attacker's address is never checked. The allowlist is fully bypassed.

## Impact Explanation

A curated pool (restricted to institutional traders, KYC'd users, or specific counterparties) can be accessed by any unpermissioned user through the public router. LP funds are consumed by unauthorized swaps — LPs suffer adverse selection from actors the pool was explicitly designed to exclude. If the pool holds concentrated liquidity at oracle-anchored prices, unauthorized users can extract value at favorable oracle prices reserved for allowlisted parties. This is a direct bypass of an explicit access-control guard with fund-impacting consequences on curated pools, matching the "admin-boundary break" and "broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entry point. Allowlisting it alongside specific users is a natural and expected admin action — not a misconfiguration. Any user who discovers the bypass can exploit it immediately with zero cost beyond gas. No privileged setup is required by the attacker; a normal router call suffices. The condition is repeatable and persistent until the router is de-allowlisted (which would break legitimate periphery access).

## Recommendation

Pass the original end user through the swap call chain so the extension can gate the correct actor. The preferred fix is to add a `swapper` parameter to `pool.swap()` that the router fills with `msg.sender` (the end user). The pool enforces `swapper == msg.sender` for direct calls and passes this value as `sender` to extensions. Apply the same pattern as `DepositAllowlistExtension` uses for `owner` — an explicit beneficiary parameter that represents the economic actor, not the intermediary.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: allowedSwapper[pool][router] = true   (router allowlisted)
  - Pool admin: allowedSwapper[pool][attacker] = false (attacker NOT allowlisted)
  - Pool has liquidity

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=attacker, ...)
    → pool: _beforeSwap(msg.sender=router, ...)
    → ExtensionCalling encodes sender=router, calls SwapAllowlistExtension.beforeSwap
    → allowedSwapper[pool][router] == true → PASSES
    → swap executes, attacker receives tokens

Result:
  Non-allowlisted attacker successfully swaps on a curated pool.
  The allowlist guard is completely bypassed.
  LP funds are consumed by an unauthorized actor.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
