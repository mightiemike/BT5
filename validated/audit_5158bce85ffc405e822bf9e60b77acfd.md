Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Real User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the pool admin allowlists the router to enable standard periphery access for their curated users, every unprivileged user can bypass the allowlist by routing through the router. No configuration exists that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` — the direct caller of `pool.swap()` — as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist, where `msg.sender` inside the extension is the pool:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original user's address:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

When a user calls `exactInputSingle`, the pool sees `msg.sender = router`. The extension then evaluates `allowedSwapper[pool][router]`. If the admin has allowlisted the router (the natural step to let curated users use the standard periphery), the check passes for **any** caller of the router, regardless of whether that caller is on the allowlist.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`.

Existing guards are insufficient: `BaseMetricExtension.onlyPool` only verifies the caller is a registered pool; it does not verify the identity of the original user. There is no mechanism in the extension, pool, or router to attest the real user's address through the call stack.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (KYC'd addresses, institutional partners, protocol-controlled accounts) is fully bypassed. Any unprivileged EOA can execute swaps against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This exposes LP funds to unrestricted market participants, directly contradicting the pool's configured access policy. This constitutes a broken core pool functionality causing potential LP principal loss if the pool was sized for a controlled trading environment, and an admin-boundary break where the pool admin's configured access control is bypassed by an unprivileged path.

## Likelihood Explanation

The router is the canonical, documented periphery entry point. Pool admins who want their allowlisted users to have a normal UX will allowlist the router as a matter of course. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool. The attack is repeatable, costless beyond gas, and requires zero setup beyond calling the public router function.

## Recommendation

The pool must forward the original user's identity through the call stack. The most robust fix is to have the extension decode the real user from `extensionData` when `sender` is a known trusted router, and check that decoded address against the allowlist. Alternatively, the router can encode `msg.sender` into `extensionData` and the extension can verify it — but this requires a trust assumption that the router is not spoofing the address. A registry of trusted routers in the extension, combined with mandatory `extensionData` attestation for router-originated calls, would close the bypass while preserving usability.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to let their allowlisted users use the standard periphery)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) → msg.sender in pool = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for attacker despite attacker not being on the allowlist

Foundry test sketch:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook
  2. swapExtension.setAllowedToSwap(pool, address(router), true)
  3. Assert attacker (not on allowlist) can call router.exactInputSingle and swap succeeds
  4. Assert attacker calling pool.swap() directly reverts with NotAllowedToSwap
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
