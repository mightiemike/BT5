Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call — the router's address when swaps are routed through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router so that even one legitimate user can swap via the router simultaneously opens the gate to every address on-chain. The per-user allowlist is rendered a no-op for the entire router path.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller of the pool) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate pool caller: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)`, the pool's `msg.sender` is the router contract, not the end-user: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The `recipient` field — which the router always sets to the user-supplied address — is passed as the second argument to `beforeSwap` but is silently discarded (the second parameter is unnamed `address`). No existing guard in the pool, the router, or the extension checks the identity of the economic actor on the router path.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The moment the router is allowlisted (a prerequisite for any allowlisted user to use the router), every address on-chain can execute swaps against the pool's LP reserves. LP funds are exposed to trading by parties the pool admin explicitly excluded, breaking the pool's intended access-control invariant and constituting a broken core pool functionality with direct fund impact on LPs.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` function on the router. The precondition (router allowlisted) is a natural operational state for any pool that wants allowlisted users to use the router.

## Recommendation
The extension must gate on the identity of the economic actor, not the immediate pool caller. The `recipient` argument is already passed to `beforeSwap` as the second parameter and is the address that receives output tokens. Checking `allowedSwapper[msg.sender][recipient]` instead of `allowedSwapper[msg.sender][sender]` would correctly identify the economic beneficiary, since `MetricOmmSimpleRouter` always sets `recipient` to the user-supplied address. Alternatively, an `originSender` field can be added to `extensionData` that the router populates with `msg.sender`, with the extension decoding and verifying it against a trusted-forwarder registry.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` and calls `setAllowedToSwap(pool, alice, true)`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that `alice` can use `MetricOmmSimpleRouter`.
3. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. The router calls `pool.swap(recipient=bob, ...)` — pool's `msg.sender` is the router.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes. `bob` receives output tokens despite `allowedSwapper[pool][bob] == false` never being consulted.

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
