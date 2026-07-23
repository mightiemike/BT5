### Title
`SwapAllowlistExtension` Bypassed via Router — Any User Can Swap in a Restricted Pool - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the first argument forwarded by the pool — which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for any user), the per-user allowlist is completely bypassed and every user can swap freely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument passed by the pool — which the pool sets to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-231
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient, ...
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The pool's `msg.sender` is the router contract, so the extension sees `sender = router`. The extension then checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

- **Option A:** Do not allowlist the router → allowlisted users cannot use the router at all (broken UX).
- **Option B:** Allowlist the router → every user on the network can bypass the allowlist by routing through the router.

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users while blocking non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, or whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The guard that the pool admin believed was enforcing access control silently fails to gate the actual end user. This is a broken core pool functionality / admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) circumvents a factory/pool admin-configured guard.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router address, at which point the bypass is immediately available to all users. The router is a public, permissionless contract. Likelihood is **High** whenever the pool admin allowlists the router to support normal router-mediated swaps.

---

### Recommendation

The extension must gate on the actual end user, not the intermediary. Two approaches:

1. **Pass the original caller through the router:** The router should forward `msg.sender` (the actual user) as an explicit `sender` field in `extensionData`, and the extension should decode and verify it. This requires a convention between the router and extensions.

2. **Check `sender` only when it is not a trusted router; otherwise check a user field from `extensionData`:** The extension can maintain a registry of trusted routers and, when `sender` is a trusted router, decode the actual user from `extensionData`.

3. **Simplest fix:** Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level (e.g., revert pool creation if both are configured together without an explicit override).

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, allowedUser, true)` — only `allowedUser` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — to allow `allowedUser` to swap via the router.
4. `attackerUser` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attackerUser` successfully swaps in a pool they were never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
