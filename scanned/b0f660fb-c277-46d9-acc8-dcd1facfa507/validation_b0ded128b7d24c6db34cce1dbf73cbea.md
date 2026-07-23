### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every user on the network can bypass the allowlist by routing through the router.

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
          [pool's msg.sender = router address]
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          checks: allowedSwapper[pool][router]
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this as the first argument to the extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` (the router) is allowlisted for the pool:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`.

This creates an inescapable dilemma for any pool admin who wants to restrict swaps to specific users:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all (their direct-pool swaps work, but `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` all revert).
- **If the router IS allowlisted**: every user on the network can bypass the allowlist by calling any router entry point, because the extension sees only the router address and approves it.

There is no mechanism in the current design for the router to pass the original `msg.sender` to the pool in a way the extension can verify. The router stores the payer in transient storage for callback settlement, but this is never surfaced to the extension.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) is fully open to any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege; they only need to call a public router function. The pool's LP assets are exposed to trades from actors the pool admin explicitly intended to exclude, which can result in:

- Unauthorized extraction of LP value by disallowed traders.
- Complete failure of the pool's curation policy.
- Broken core pool functionality for pools whose design depends on a restricted trading set.

### Likelihood Explanation

The likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and wants their allowlisted users to use the router must allowlist the router, at which point the bypass is immediately available to all users. The attacker requires no special setup, no privileged access, and no unusual token behavior—only a standard router call.

### Recommendation

The `sender` identity forwarded to extensions must represent the economic actor, not the intermediary contract. Two approaches:

1. **Pass original user through extension data**: Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a signature or trusted-forwarder pattern).
2. **Pool-level original-sender tracking**: Have the pool accept an explicit `originator` parameter (separate from `msg.sender`) that the router populates with its own `msg.sender`, and forward this to extensions as the identity to gate.

Either approach must ensure the identity cannot be spoofed by an arbitrary caller.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as EXTENSION_1,
         BEFORE_SWAP_ORDER = encodeExtensionOrder(1, 0, 0, 0, 0, 0, 0)
  admin calls: swapExtension.setAllowedToSwap(pool, userA, true)
  admin calls: swapExtension.setAllowedToSwap(pool, address(router), true)
    // ↑ required so userA can use the router

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle(ExactInputSingleParams{
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000,
        ...
    })

  Execution:
    router → pool.swap(...)          // pool sees msg.sender = router
    pool   → _beforeSwap(router, ...)
    ext    → allowedSwapper[pool][router] == true  → passes
    swap executes for userB          // allowlist bypassed
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
