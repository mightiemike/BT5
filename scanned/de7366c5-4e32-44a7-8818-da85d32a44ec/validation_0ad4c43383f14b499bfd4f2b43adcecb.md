### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When users swap through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The allowlist therefore gates the router address rather than the individual user, making per-user swap restrictions either trivially bypassable (if the router is allowlisted) or incorrectly blocking legitimate allowlisted users (if the router is not allowlisted).

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded from `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, i.e. the direct caller of swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the allowlist lookup becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`.

This produces two broken states depending on how the pool admin configures the allowlist:

**Scenario A — Bypass**: The pool admin allowlists the router address (the only way to permit router-mediated swaps). Any unprivileged user can now bypass the per-user allowlist by routing through the public `MetricOmmSimpleRouter`. The allowlist is completely ineffective.

**Scenario B — Lock**: The pool admin allowlists specific user addresses but not the router. Those allowlisted users cannot swap through the router even though they are individually permitted. They are silently forced to call the pool directly, which is not the standard user flow and may not be possible for integrators.

### Impact Explanation

- **Scenario A** is a direct allowlist-boundary break: an unprivileged actor reaches a swap path the pool admin intended to restrict. Any volume, fee, or price-impact consequence of those swaps is unauthorized.
- **Scenario B** is broken core pool functionality: the standard swap entry point (`MetricOmmSimpleRouter`) is unusable for allowlisted users, effectively locking them out of the pool's swap flow.

Both outcomes satisfy the allowed impact gate: admin-boundary break (allowlist bypassed by an unprivileged path) and broken core pool functionality causing unusable swap flows.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any production pool that deploys `SwapAllowlistExtension` expecting per-user gating will immediately exhibit one of the two broken states. The bypass (Scenario A) is trivially reachable by any user who calls the public router. No special privileges, flash loans, or multi-step setup are required.

### Recommendation

The extension must check the identity of the economic actor, not the intermediary. Two viable approaches:

1. **Pass the original user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct value, which is acceptable since the router is a known periphery contract.

2. **Check `sender` only for direct calls; require a signed proof for router calls**: The extension verifies a user-signed allowlist proof embedded in `extensionData`, making the check router-agnostic.

At minimum, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must not be deployed on pools that expect router access.

### Proof of Concept

**Bypass path (Scenario A)**:

```
1. Pool admin deploys pool with SwapAllowlistExtension on beforeSwap.
2. Admin calls extension.setAllowedToSwap(pool, router, true)
   (required to allow any router swap at all).
3. Non-allowlisted user calls:
     router.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. Non-allowlisted user completes swap on a restricted pool.
```

**Lock path (Scenario B)**:

```
1. Pool admin deploys pool with SwapAllowlistExtension on beforeSwap.
2. Admin calls extension.setAllowedToSwap(pool, alice, true).
   Router is NOT allowlisted.
3. Alice calls router.exactInputSingle({pool: pool, ...}).
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. Extension evaluates: allowedSwapper[pool][router] == false → NotAllowedToSwap().
6. Alice, despite being individually allowlisted, cannot swap through the router.
```

**Relevant code locations**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
