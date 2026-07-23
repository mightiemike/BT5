### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the extension checks whether the **router** is allowlisted — not the original user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every user can bypass the swap allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument, which the pool sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router does not forward the original user's address to the pool. The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

This creates an irreconcilable dilemma for pool admins:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ✗ Blocked (usability broken) | ✓ Blocked |
| Yes | ✓ Allowed | ✗ **Also allowed — bypass** |

If the pool admin allowlists the router so that allowlisted users can use the standard periphery, every user gains the same access.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific participants (e.g., KYC'd users, institutional counterparties) is fully bypassed by any user routing through `MetricOmmSimpleRouter`. The non-allowlisted user executes real swaps against pool liquidity, receiving output tokens and paying input tokens through the router's callback. This is a direct curation failure and constitutes broken core pool functionality: the configured guard fails open on the standard public swap path.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the protocol's primary user-facing swap entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the pool to all users. The admin has no way to distinguish router-mediated calls by original user without a protocol-level fix. The bypass requires no special privileges — any EOA can call `exactInputSingle`.

### Recommendation

The extension must check the economically relevant actor — the original user — not the direct pool caller. Two approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.

2. **Pool-side**: Add a transient-storage slot on the pool that the router populates with the original user before calling `swap()`, and have the extension read from it.

Either way, the extension's actor binding must be changed so that `allowedSwapper[pool][originalUser]` is checked, not `allowedSwapper[pool][router]`.

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists Alice (intended swapper)
ext.setAllowedToSwap(pool, alice, true);
// Pool admin also allowlists the router so Alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (not allowlisted) routes through the router
// router.exactInputSingle() → pool.swap(msg.sender=router) → ext.beforeSwap(sender=router)
// ext checks allowedSwapper[pool][router] → true → swap succeeds
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    tokenIn: token1,
    extensionData: ""
}));
// Bob's swap succeeds despite not being on the allowlist
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
