### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` seen by the pool is the router contract, not the actual end-user. If the pool admin allowlists the router address (which is required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user allowlist by routing through the public router.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the end-user). When the router is allowlisted, the check passes for every caller of the router regardless of their individual allowlist status.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

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

The pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → even individually-allowlisted users cannot use the router.
- **Allowlist the router** → every user on-chain can bypass the per-user allowlist by routing through the public router.

### Impact Explanation

Any user can execute swaps on a pool that has `SwapAllowlistExtension` configured to restrict trading to specific addresses. The allowlist is completely defeated for router-mediated swaps. Pools using this extension for regulatory compliance, KYC gating, or LP-protection purposes will silently accept swaps from any address. This constitutes broken core pool functionality (access control) with direct fund-impacting consequences: unauthorized parties can drain LP positions through unrestricted swaps.

### Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` with any pool address. No special privilege, flash loan, or complex setup is required. The only precondition is that the router is allowlisted, which the pool admin must do to allow any router-mediated swap at all.

### Recommendation

The extension must check the actual end-user identity, not the immediate pool caller. Two approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData`, and have `SwapAllowlistExtension` decode and verify it. The extension should also verify that `sender` (the pool caller) is a known trusted router before trusting the decoded address.

2. **Direct-only enforcement**: Document that `SwapAllowlistExtension` only works correctly for direct pool calls (not router-mediated), and add an explicit check that reverts if `sender` is not in the allowlist regardless of router status — forcing users to call the pool directly.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router must be allowlisted for alice to use it.
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, bob, ...) with sender = router.
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes successfully — allowlist bypassed.
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
