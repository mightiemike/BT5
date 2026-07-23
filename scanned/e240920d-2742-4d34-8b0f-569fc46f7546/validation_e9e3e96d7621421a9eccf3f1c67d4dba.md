### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` on the pool, so `sender = router`. If the pool admin allowlists the router (the only way to enable router-mediated swaps), every user on the network can bypass the individual allowlist by routing through the public router contract.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `sender = address(router)`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an inescapable dilemma:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router at all |
| Yes | **Every** user on the network can bypass the individual allowlist via the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

### Impact Explanation

A pool deploying `SwapAllowlistExtension` is explicitly curated — it may represent a KYC-gated venue, a private market-maker pool, or a pool whose LP terms assume only vetted counterparties. Once the router is allowlisted (the only way to support the standard periphery path), any unprivileged address can call `router.exactInputSingle` and execute swaps in the restricted pool. Unauthorized swappers can extract value from LPs at oracle-anchored prices the pool was not designed to offer them, constituting a direct loss of LP principal. The allowlist guard fails completely open for the router path.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing entry point documented in the protocol. Any pool admin who wants their allowlisted LPs or counterparties to use the standard router must allowlist it, triggering the bypass. The attacker needs no special role, no privileged setup, and no non-standard token — only a call to the public router.

### Recommendation

The extension must identify the **economic actor**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address. The pool admin allowlists real users, not the router.

2. **Recipient-based check**: For swap allowlists, gate on `recipient` rather than `sender` when the pool is configured to use a trusted router, since `recipient` is the address that actually receives the output tokens and is harder to spoof.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin allowlists Alice (allowedSwapper[pool][Alice] = true)
  pool admin allowlists router (allowedSwapper[pool][router] = true)
    ↑ required so Alice can use the router

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: Bob, ...})

  router calls:
    pool.swap(Bob, zeroForOne, amount, limit, "", extensionData)
    // msg.sender = router

  pool calls:
    extension.beforeSwap(sender=router, recipient=Bob, ...)

  extension evaluates:
    allowedSwapper[pool][router] == true  ✓  (router is allowlisted)

  Result: Bob's swap executes in the restricted pool.
  Bob extracts value at oracle-anchored prices the pool was not designed to offer him.
  LPs suffer the loss.
```

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
