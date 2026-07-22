### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user on the router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact address against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly without forwarding the original user's address:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

There is no mechanism for the router to inject the originating EOA into the `sender` slot. The pool always sees `msg.sender = router`.

**Structural impossibility**: A pool admin who wants to allow their allowlisted users to also use the router has exactly two options:

| Configuration | Effect |
|---|---|
| Allowlist individual users only | Allowlisted users cannot use the router (router is not allowlisted → reverts) |
| Allowlist the router | Every user on the router can bypass the allowlist |

There is no configuration that correctly restricts router-mediated swaps to only the intended users.

---

### Impact Explanation

When a pool admin allowlists `MetricOmmSimpleRouter` to support router-mediated swaps for their curated counterparties, any unprivileged user can call `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting that pool and the `beforeSwap` hook will pass — `allowedSwapper[pool][router] == true`. The allowlist is completely bypassed. LPs in a pool designed for trusted-only counterparties are exposed to arbitrary swappers, which can lead to direct LP value loss if the pool's economics depend on counterparty selection (e.g., RWA pools, institutional pools, or pools with asymmetric oracle pricing).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for end users. A pool admin who deploys a curated pool and wants their allowlisted users to benefit from multi-hop routing or slippage protection will naturally allowlist the router. The admin has no way to achieve "router-mediated swaps for specific users only" — the only path to enabling router access is the one that opens the pool to everyone. This makes the misconfiguration highly probable in practice.

---

### Recommendation

The `sender` forwarded to extension hooks must represent the economic actor, not the intermediary. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` in `extensionData` (or a dedicated field) so extensions can recover the real user.
2. **Extension-side**: `SwapAllowlistExtension` should decode and check the real user from `extensionData` when the direct `sender` is a known router, or the pool should expose a standardised "originator" field in the hook arguments.

Alternatively, document explicitly that allowlisting the router opens the pool to all router users, and provide a separate `RouterSwapAllowlistExtension` that reads the originator from a signed payload in `extensionData`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended curated user
  allowedSwapper[pool][router] = true         // admin adds router to support alice's router usage

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓ passes
        → swap executes, bob receives output tokens

Result: bob, who is not on the allowlist, successfully swaps in a curated pool.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
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
