### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router contract address, not the actual end user. The allowlist therefore gates at the router level, not the individual user level. If the router is allowlisted (necessary for any allowlisted user to use the router), every unpermissioned user can bypass the restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of the pool
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

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

At this point `msg.sender` inside the pool is the **router**, so `sender = router` is what the extension sees. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irreconcilable dilemma for any pool admin who configures a swap allowlist:

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Every unpermissioned user bypasses the allowlist by routing through the router |
| **No** | Even allowlisted users cannot use the router (broken functionality) |

There is no configuration that simultaneously allows allowlisted users to use the router while blocking non-allowlisted users.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the position owner explicitly passed to `addLiquidity`), which is preserved through the `MetricOmmPoolLiquidityAdder` call chain. The swap path has no equivalent separate "user" parameter — the actual initiator's identity is lost when the router intermediates.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a permissioned set of market makers or counterparties can be accessed by any unpermissioned user through the router. This breaks the core access-control invariant of the allowlist extension, allowing unauthorized swaps that can:

- Extract value from the pool through arbitrage
- Cause price impact that harms LP principal
- Circumvent any rate-limiting or compliance intent encoded in the allowlist

The pool's LP funds are directly at risk from unauthorized swap activity.

---

### Likelihood Explanation

The bypass is reachable by any user who calls `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` against a pool with `SwapAllowlistExtension` active. No special privileges are required. The only precondition is that the router address is allowlisted (which is necessary for any allowlisted user to use the router at all), making the bypass a natural consequence of normal pool operation.

---

### Recommendation

The `beforeSwap` hook signature does not carry the end user's identity separately from the direct pool caller. Two remediation paths exist:

1. **Pass the original user through `extensionData`**: The router should encode the original `msg.sender` into `extensionData`, and the extension should decode and check it. This requires a coordinated change to the router and extension.

2. **Check `recipient` as a proxy**: If the pool admin's intent is to gate who *receives* output, `recipient` can be checked instead of `sender`. This is semantically different but may match the intended policy.

3. **Document the limitation**: If the allowlist is only intended to gate direct pool callers (not end users), document that it is incompatible with router-mediated swaps and that the router must never be allowlisted.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can use the router.
3. Pool admin calls setAllowedToSwap(pool, alice, true)
   — alice is the intended permissioned swapper.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool=pool, recipient=bob, ...
   ).
5. Router calls pool.swap(bob, ...) with msg.sender=router.
6. Pool calls _beforeSwap(sender=router, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes despite not being on the allowlist.
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
