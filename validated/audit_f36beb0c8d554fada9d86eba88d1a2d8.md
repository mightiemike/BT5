Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router intermediary address instead of the actual end-user, allowing any caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address, not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for legitimate users), the allowlist is silently bypassed for every caller of the router, including non-allowlisted addresses.

## Finding Description
`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The router does not forward the original `msg.sender` (the actual user) anywhere in the `pool.swap()` call. As a result, the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

This creates an inescapable dilemma for the pool admin:
- Router **not** allowlisted → all router-mediated swaps revert, even for allowlisted users
- Router **allowlisted** → any user (including non-allowlisted) can bypass the guard via the router

No existing guard compensates for this: the extension has no mechanism to distinguish the economic actor from the intermediary, and the pool passes no additional identity context.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses) is fully bypassed for any caller of `MetricOmmSimpleRouter` once the router is allowlisted. Non-allowlisted users can execute swaps on the curated pool, accessing oracle-anchored prices the pool admin intended to reserve for specific counterparties. The allowlist guard — the sole access-control mechanism on the swap path — provides no protection. This constitutes a broken core pool functionality causing potential loss of funds and an admin-boundary break where an unprivileged path bypasses a configured access control.

## Likelihood Explanation
The pool admin must allowlist the router to support router-mediated swaps for their legitimate allowlisted users. This is the natural and expected configuration for any curated pool that also wants to support the standard periphery. The bypass is therefore reachable in any realistic deployment of `SwapAllowlistExtension` with the router. Any unprivileged user can exploit this by simply calling `router.exactInputSingle` (or any other router swap function) targeting the curated pool. No special privileges, flash loans, or complex setup are required beyond knowing the pool address.

## Recommendation
The extension must check the economic actor (the end user), not the intermediary. Two approaches:

1. **Trusted-forwarder pattern**: Recognize the router as a trusted forwarder. The router encodes the real caller's address in a standardized slot in `extensionData`, and the extension decodes and checks it when `sender` is a recognized forwarder address.

2. **Router forwards original caller**: Modify the pool interface to accept an explicit `originalSender` parameter for whitelisted periphery contracts, and have the router pass `msg.sender` through. The extension then checks `originalSender` when the caller is a trusted router.

Avoid `tx.origin` as it breaks compatibility with smart contract wallets and introduces phishing risks.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured; set `allowAllSwappers[pool] = false`.
2. Allowlist only `alice` and the `MetricOmmSimpleRouter` address: `allowedSwapper[pool][alice] = true`, `allowedSwapper[pool][router] = true`.
3. As `bob` (not allowlisted), call `router.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(...)` → pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` = `true` → passes.
5. Bob's swap executes on the curated pool despite not being allowlisted.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
