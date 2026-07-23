### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument passed by the pool's `_beforeSwap` hook. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the user. Any pool admin who allowlists the router to enable legitimate users to trade through it simultaneously opens the pool to every address on-chain, completely defeating the allowlist guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads the `sender` argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

At this point `msg.sender` inside `pool.swap` is the **router**, not the original user. The extension therefore evaluates `allowedSwapper[pool][router]`. The actual user's address is never consulted.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-guarded pool intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses). To allow those addresses to use the standard periphery router, the admin must allowlist the router address. The moment the router is allowlisted, **every address on-chain** can call `exactInputSingle` / `exactOutput` / `exactOutputSingle` through the router and pass the guard, because the extension only sees `allowedSwapper[pool][router] == true`. Unauthorized swappers can then extract value from the pool at oracle-derived prices, draining LP assets that were deposited under the assumption of a closed, permissioned pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token, or setup is required. Any user who observes that a pool has a swap allowlist and that the router is allowlisted can immediately exploit this by calling the router. The router is the canonical entry point for swaps, so pool admins are likely to allowlist it as a matter of course.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field in `extensionData` (signed or verified via a trusted forwarder pattern), and the extension should decode and verify it.

2. **Alternatively, check `sender` at the router level**: The router could expose a `swapOnBehalf(address user, ...)` entry point that is itself access-controlled, so the pool's `msg.sender` is always the router but the extension can trust a verified `user` field in `extensionData`.

The simplest short-term fix is to require that `pool.swap` is called directly (not through the router) for allowlisted pools, and document this constraint clearly. Longer term, the extension interface should include a verified originator field.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
3. Admin also calls `setAllowedToSwap(pool, router, true)` so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — pool's `msg.sender` is `router`.
6. `_beforeSwap(sender=router, ...)` is dispatched to `SwapAllowlistExtension`.
7. Extension checks `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes against the restricted pool, extracting LP assets.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
