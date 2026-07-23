### Title
SwapAllowlistExtension Gates on Router Address Instead of Originating User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the public internet can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the value the pool passes — which is `msg.sender` of the original `pool.swap()` call. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool receives `msg.sender = router`. It passes `sender = router` to `_beforeSwap`, which forwards it to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert `NotAllowedToSwap` — router is unusable for this pool |
| **Allowlist the router** | Every public user can bypass the per-user allowlist by calling the router |

The router is a permissionless public contract with no caller-level access control. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only, or regulatory-restricted) and configures `SwapAllowlistExtension` to gate specific swappers cannot prevent non-allowlisted users from trading if the router is allowlisted. The allowlist guard is silently bypassed: the swap executes at the live oracle price, the pool's bin state changes, and the non-allowlisted user receives output tokens. This breaks the core curation invariant — "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" — and constitutes an admin-boundary break by an unprivileged path.

---

### Likelihood Explanation

The router is the primary UX path for end users. Any pool that wants to support router-mediated swaps must allowlist the router, which immediately opens the bypass to all users. The attacker needs no special privilege, no unusual token, and no complex setup — a single call to `exactInputSingle` suffices.

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers and, when `sender` is a router, falls back to checking a user identity embedded in `extensionData`.
3. **Require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension level by reverting when `sender` is a known router address.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)  // to enable router swaps
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) with msg.sender = router
6. Pool calls beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true → passes
8. Swap executes; attacker receives output tokens despite not being allowlisted.
``` [1](#0-0) [3](#0-2) [5](#0-4)

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
