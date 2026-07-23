### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender = router`. If the pool admin allowlists the router (a necessary step for any router-mediated swap to work for allowlisted users), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct caller) is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap()` directly:

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

The pool receives `msg.sender = router`. It passes `router` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Attack path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a set of trusted addresses (e.g., Alice).
2. Alice wants to use the router, so the admin also allowlists the router address.
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
4. The router calls `pool.swap()` with `msg.sender = router`.
5. The extension sees `sender = router`, which is allowlisted → check passes.
6. Bob's swap executes in the restricted pool, bypassing the allowlist entirely.

The bypass requires zero privilege: any EOA or contract can call the public router.

### Impact Explanation

The `SwapAllowlistExtension` is a production guard designed to enforce access control on swaps in permissioned pools. Its complete bypass allows unauthorized users to execute swaps against pools that were intended to be restricted. Consequences include:

- Unauthorized price-moving swaps against LP positions in a pool designed for controlled market-making.
- LP assets drained or mispriced by actors the pool admin explicitly excluded.
- The core pool invariant — "only allowlisted addresses may swap" — is broken for any pool that also needs router support.

This is a direct loss-of-LP-assets / broken-core-pool-functionality impact.

### Likelihood Explanation

**Medium.** The precondition is that the pool admin allowlists the router address (to allow allowlisted users to use the router). This is a natural and expected operational step: without it, allowlisted users cannot use the standard periphery. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special knowledge or capital beyond the swap amount.

### Recommendation

The `SwapAllowlistExtension` should gate on the **economic actor** (the end user), not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward the original `msg.sender` as an additional field in `extensionData`, and the extension should decode and check it. Alternatively, the pool could expose a `swapWithOriginator` entry point.

2. **Check `sender` against a router-aware allowlist.** If the `sender` is a known router, the extension should decode the true initiator from `extensionData` and check that address instead.

3. **Short-term mitigation:** Document that allowlisting the router is equivalent to `allowAll = true`, and require pool admins to use `setAllowAllSwappers` explicitly rather than allowlisting the router address.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER configured)
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)  ← needed for Alice to use router

Attack:
  - Bob (not allowlisted) calls:
      router.exactInputSingle({
        pool: restrictedPool,
        recipient: bob,
        zeroForOne: true,
        amountIn: X,
        ...
      })

  - router calls pool.swap(bob_recipient, true, X, ...) with msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true (allowlisted)
  - swap executes; Bob receives tokens from the restricted pool
  - Alice's LP position is exposed to Bob's unauthorized swap
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
