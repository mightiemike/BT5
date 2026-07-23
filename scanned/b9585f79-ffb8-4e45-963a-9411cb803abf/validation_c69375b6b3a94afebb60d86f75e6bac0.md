### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the pool's `msg.sender` (i.e., the immediate caller of `pool.swap()`). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router to enable router-mediated swaps for their curated users, every unprivileged address can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of `beforeSwap`) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = pool's msg.sender
  )
);
```

The pool passes its own `msg.sender` as `sender`. When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap(...)`, the pool's `msg.sender` is the router:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

**Consequence**: A pool admin who wants their allowlisted users to be able to use the standard router must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller regardless of who the actual end-user is. Any non-allowlisted address can bypass the curated gate by calling `router.exactInputSingle(...)` instead of `pool.swap(...)` directly.

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths, and to any future public periphery contract that calls `pool.swap()`.

---

### Impact Explanation

**High.** The `SwapAllowlistExtension` is the sole on-chain mechanism for curated pools to restrict who may trade. A complete bypass means:

- Non-KYC'd / non-allowlisted addresses can execute swaps against a pool that was explicitly configured to exclude them.
- LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to the full public order flow, including adversarial MEV and front-running.
- Any regulatory or compliance guarantee the pool admin intended to enforce is nullified.

This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"* and *"Broken core pool functionality causing loss of funds."*

---

### Likelihood Explanation

**Medium-High.** The bypass is reachable whenever the pool admin allowlists the router — a natural and expected configuration step. A pool admin who wants their allowlisted users to be able to use the standard periphery router (the primary user-facing swap interface) must allowlist the router address. The moment they do, the guard is open to everyone. Because the router is the canonical swap entrypoint documented in the protocol, this misconfiguration is highly probable in any real deployment of a curated pool.

---

### Recommendation

The extension must check the economic actor, not the immediate caller. Two sound approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed payload.
2. **Check `sender` only for direct pool calls; require a separate allowlist entry for router-mediated flows**: The extension could distinguish between a known router address and an EOA, and for router calls, decode the real user from `extensionData`.

The simplest safe fix is to document that the `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is a known router, forcing direct pool interaction for curated pools.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes against the curated pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
