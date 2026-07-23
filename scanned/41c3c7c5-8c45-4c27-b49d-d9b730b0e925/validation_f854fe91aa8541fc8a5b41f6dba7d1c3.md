### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is `msg.sender` from the pool's perspective. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the **router address**, not the actual end-user. If the pool admin allowlists the router (a natural action to enable router-mediated swaps), every user—including those not individually allowlisted—can bypass the guard entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of the pool
    recipient,
    zeroForOne,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant), the router becomes the direct caller of the pool:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So `sender` inside `beforeSwap` is the **router**, not the end-user. The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`.

This creates an irreconcilable conflict for pool admins:

| Router allowlisted? | Effect |
|---|---|
| Yes | **All users bypass the allowlist** via the router |
| No | **All allowlisted users are blocked** from using the router |

There is no configuration that simultaneously allows router-mediated swaps and restricts access to specific users.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` (including the recursive callback path in `_exactOutputIterateCallback` at line 220–228), because in every case the router is the direct caller of `pool.swap()`.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., a private institutional pool) and then allowlists the router address to enable router-mediated swaps for those users inadvertently opens the pool to **all users**. Any non-allowlisted address can call `router.exactInputSingle()` and execute swaps against the restricted pool. Unauthorized swaps move the pool price and consume LP liquidity, directly harming LP principal. This matches the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "direct loss of LP assets" impact categories.

---

### Likelihood Explanation

The trigger condition—pool admin allowlisting the router—is a natural and expected operational step. The `SwapAllowlistExtension` documentation states it "Gates `swap` by swapper address, per pool," implying individual-user granularity. A pool admin who wants allowlisted users to be able to use the router will allowlist the router, unaware that this opens the pool to everyone. No privileged attacker capability is required; any EOA can call the router.

---

### Recommendation

The extension must check the **actual end-user identity**, not the intermediary router. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router wrapper**: Deploy a router that is not allowlisted but forwards the real user identity through a trusted channel (e.g., a separate allowlist that the router enforces before calling the pool).

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` as the first word of `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - allowedSwapper[pool][userA] = true       (legitimate user)
  - allowedSwapper[pool][router] = true      (admin enables router-mediated swaps)
  - allowedSwapper[pool][attacker] = false   (attacker is NOT allowlisted)

Attack:
  1. attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  2. router calls pool.swap(attacker, zeroForOne, amount, ...)
     → msg.sender to pool = router
  3. pool calls _beforeSwap(router, attacker, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router] → TRUE
     → swap is NOT reverted
  5. attacker receives output tokens; pool LP balances are moved

Result: attacker executes a swap in a pool they are explicitly not allowlisted for.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
