### Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract — not the originating EOA. If a pool admin allowlists the router to enable router-mediated swaps for their curated users, every unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. Conversely, if the router is not allowlisted, no allowlisted user can swap through the router at all.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is whoever called the pool's `swap` function: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes `sender` directly from the pool's own `msg.sender` into the extension call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

At that point, `msg.sender` inside the pool is the **router**, not the originating EOA. The pool passes the router's address as `sender` to `_beforeSwap`, and the extension evaluates `allowedSwapper[pool][router]` — a check on the router contract, not on the actual user.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To allow those users to swap through the supported periphery router, the admin must allowlist the router address. The moment the router is allowlisted, the allowlist is effectively nullified: **any** unprivileged user can call `exactInputSingle` (or any other router entry point) and the extension will pass because `allowedSwapper[pool][router] == true`. The attacker can drain the pool's liquidity at oracle-anchored prices that the pool admin intended to offer only to trusted counterparties.

The alternative — not allowlisting the router — breaks the router for all users, including legitimate ones, making the pool's curated swap functionality unusable through the supported periphery path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point for EOAs. Any pool that deploys `SwapAllowlistExtension` and wants its allowlisted users to be able to swap through the router will inevitably allowlist the router, triggering the bypass. The attack requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call from any address suffices.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two complementary approaches:

1. **Pass the original caller through the router**: The router should forward `msg.sender` (the EOA) as an explicit `sender` field in `extensionData`, and the extension should decode and verify it. This requires a coordinated change to the router and extension interface.

2. **Check `sender` only for direct pool calls; require extensions to read the real initiator from a trusted transient context**: The pool could store the original `msg.sender` in transient storage at the top of `swap` and expose it to extensions, so the extension always sees the EOA regardless of the call path.

Until fixed, pool admins should not deploy `SwapAllowlistExtension` on pools that are expected to be reachable through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E.
  - Pool admin allowlists only Alice: allowedSwapper[P][Alice] = true.
  - Pool admin also allowlists the router R so Alice can use it: allowedSwapper[P][R] = true.

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
  2. Router calls P.swap(recipient, ...) — msg.sender inside P is R (the router).
  3. Pool calls ExtensionCalling._beforeSwap(sender=R, ...).
  4. Extension evaluates: allowedSwapper[P][R] == true → passes.
  5. Bob's swap executes at oracle price, bypassing the allowlist entirely.

Result:
  Bob, an unprivileged address, executes a swap on a pool that should have been
  restricted to Alice only. The pool's curated pricing is exposed to all users,
  and the LP's assets are at risk of being consumed by unauthorized flow.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
