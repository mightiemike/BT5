### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` to the pool — the router contract — not the originating user. When a pool admin allowlists the router to enable router-based swaps, every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender = router, not the originating EOA
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong actor). The allowlist is therefore keyed on `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the guard passes for every call that arrives through the router, regardless of who the originating user is. The allowlist is completely neutralised for the router path.

---

### Impact Explanation

Any user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting a pool whose `SwapAllowlistExtension` has the router address in `allowedSwapper`. The extension sees `sender = router`, the check passes, and the swap executes. The pool admin's intent to restrict swaps to a curated set of addresses is silently defeated. Unauthorized users can drain LP value through repeated swaps on a pool that was designed to be access-controlled.

---

### Likelihood Explanation

The scenario is realistic and likely:

1. A pool admin deploys a curated pool with `SwapAllowlistExtension`.
2. They want allowlisted users to be able to use the standard periphery router (the expected UX path).
3. They add the router address to `allowedSwapper` for their pool.
4. Any user can now swap on the pool through the router, bypassing the allowlist entirely.

The router is a public, permissionless contract. There is no mechanism inside the router that re-checks the originating user against the pool's allowlist.

---

### Recommendation

The allowlist must gate the economically relevant actor — the originating user — not the intermediate router. Two complementary fixes:

1. **Pass the originating user through the router.** The router already stores the payer in transient storage (`_getPayer()`). It could pass the originating `msg.sender` as a separate field in `extensionData` so the extension can decode and check it. However, this is forgeable by any caller who constructs `extensionData` manually.

2. **Check `sender` against the allowlist only when `sender` is not a known router; otherwise decode the real user from `extensionData`.** This requires a trusted router registry.

3. **Preferred: enforce the allowlist at the router level** by having the router revert if the originating `msg.sender` is not on the pool's allowlist before forwarding the call. This keeps the check at the entry point where the real user identity is known.

The safest approach is option 3 combined with documenting that direct pool calls (bypassing the router) are also subject to the allowlist — which they already are, since direct callers pass themselves as `msg.sender` to the pool.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    to enable router-based swaps for allowlisted users.
  - Alice (address not in allowedSwapper) is NOT individually allowlisted.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, zeroForOne, amount, ...).
     pool.swap sees msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. ExtensionCalling encodes beforeSwap(sender=router, ...) and calls SwapAllowlistExtension.
  5. Extension evaluates: allowedSwapper[pool][router] == true → check passes.
  6. Swap executes. Alice receives output tokens.

Result:
  Alice, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist is completely bypassed for any user who routes through MetricOmmSimpleRouter.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
