### Title
SwapAllowlistExtension Gates the Immediate Pool Caller Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end-user. If the router is allowlisted (the only way to enable router-mediated swaps for any user), every non-allowlisted user can bypass the gate by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool from its own `msg.sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool's `msg.sender` is now the **router**, so the extension checks `allowedSwapper[pool][router]` — not the end-user. For any pool that allowlists the router (the only way to permit router-mediated swaps for legitimate users), every non-allowlisted user can bypass the gate by routing through the router.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner passed explicitly), not `sender` (the immediate caller), so the deposit gate correctly identifies the economic actor regardless of whether the `MetricOmmPoolLiquidityAdder` is used.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or institutional addresses is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user can execute arbitrary swaps against the pool's LP funds, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal and fee revenue to unauthorized parties.

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is a natural and expected configuration: any pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router. Once the router is allowlisted, the bypass is trivially reachable by any public user with no special privileges. The attacker only needs to call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool.

### Recommendation

The extension must identify the end-user, not the immediate pool caller. Two approaches:

1. **Pass end-user identity via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to encode the correct identity.
2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers; when `sender` is a router, require the extension to receive the real user identity through `extensionData`.

The simplest safe fix is to not allowlist the router at all and require end-users to call `pool.swap()` directly on allowlisted pools, but this breaks router usability. The correct long-term fix is option 1 with a verified encoding scheme.

### Proof of Concept

```
Setup:
  - Pool P has SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin allowlists router R: allowedSwapper[P][R] = true
  - Alice (allowlisted): allowedSwapper[P][Alice] = true
  - Bob (not allowlisted): allowedSwapper[P][Bob] = false

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → pool's msg.sender = Router R
  3. Pool calls _beforeSwap(sender=R, ...)
  4. Extension checks: allowedSwapper[P][R] == true → passes
  5. Bob's swap executes against LP funds despite not being allowlisted

Result: Bob bypasses the swap allowlist and trades against restricted LP funds.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
