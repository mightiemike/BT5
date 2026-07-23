### Title
`SwapAllowlistExtension` Gates Direct Pool Caller Instead of Economic Actor, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When any user routes through the public `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension sees the router address as the swapper identity. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user on-chain can bypass the per-user allowlist by calling the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the economic actor
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then uses that `sender` value to look up the allowlist, keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

The pool sees `msg.sender = router`, so `sender = router` reaches the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Consequence**: A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. The moment they do, `allowedSwapper[pool][router] = true` passes the check for every caller of the router — including users who were never individually allowlisted. The per-user gate is silently voided.

This is structurally different from `DepositAllowlistExtension`, which correctly gates the `owner` (the position recipient, the economically relevant actor), not the `sender` (the payer/operator). The swap extension has no equivalent forwarding of the true initiator.

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through the public `MetricOmmSimpleRouter`. The allowlist is an admin-configured access control boundary (e.g., KYC gate, institutional-only pool, compliance restriction). Once the router is allowlisted — a necessary step for the pool to be usable via the standard periphery — the boundary is fully open to unprivileged actors. This is an admin-boundary break: an access control configured by the pool admin is bypassed by an unprivileged path through a public contract.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point for swaps. Any pool that deploys a `SwapAllowlistExtension` and also wants to support the router must allowlist the router. This is the expected operational configuration, making the bypass reachable in every realistic restricted-pool deployment.

### Recommendation

The extension should gate the economic actor, not the intermediary. Two options:

1. **Pass the true initiator through the router**: Have the router encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the actual user identity to be passed in `extensionData` and verified there.

The simpler and safer fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes and checks that address against the allowlist instead of the raw `sender`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (extension1) configured on beforeSwap.
  - Pool admin allowlists alice: allowedSwapper[pool][alice] = true
  - Pool admin allowlists the router: allowedSwapper[pool][router] = true
    (required for alice to use the router)

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool, zeroForOne, amountIn, ..., extensionData
    )
  - Router calls pool.swap(recipient, zeroForOne, ..., extensionData)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Bob's swap executes in the restricted pool.

Result:
  - Bob, who was never individually allowlisted, successfully swaps in a pool
    the admin intended to restrict to alice only.
  - The per-user allowlist invariant is broken for all router-mediated swaps.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
