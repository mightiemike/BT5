### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for any user), the allowlist check passes for every caller regardless of their individual allowlist status, completely defeating the guard.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes and dispatches this `sender` to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` to the pool is the router contract. The `sender` forwarded to `beforeSwap` is therefore the router address, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**The dilemma is structurally identical to the Derby `maxTrainingDeposit` bypass:**

| Derby | Metric OMM |
|---|---|
| User transfers balance to a second account so the per-user cap check sees zero balance | User routes through the router so the per-user allowlist check sees the router address |
| Guard checks the wrong account | Guard checks the wrong address |

Two outcomes, both harmful:

1. **Bypass (primary impact):** The pool admin must allowlist the router address to permit any router-mediated swap for legitimate users. Once the router is allowlisted, `allowedSwapper[pool][router] == true` and the check passes for every caller regardless of their individual status. Any non-allowlisted user can bypass the guard by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`.

2. **Broken functionality (secondary impact):** If the pool admin does not allowlist the router, every allowlisted user who tries to swap through the router is rejected with `NotAllowedToSwap`, making the standard periphery path unusable for the pool.

---

### Impact Explanation

The swap allowlist is the primary access-control mechanism for restricting who may trade in a pool (e.g., KYC-gated pools, institutional-only pools, beta-access pools). When the bypass is active, any address can execute swaps against the pool's liquidity, draining LP value through unrestricted trading that the pool admin explicitly intended to prevent. This is a broken core pool functionality causing loss of funds or unusable swap flows.

---

### Likelihood Explanation

The router is the canonical user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the expected operational path. The bypass is therefore reachable in any realistic deployment where the pool admin has enabled router access, which is the common case.

---

### Recommendation

The extension must gate on the economically relevant actor — the end user — not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`:** Have the router encode `msg.sender` (the end user) into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`.

2. **Check `sender` only when it is not a known router, and fall back to `extensionData`-encoded user otherwise:** The extension reads a user address from `extensionData` when `sender` is a registered router, and checks `sender` directly when it is not.

Either way, the allowlist lookup must resolve to the address that controls the economic decision to swap, not the contract that mechanically forwards the call.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is allowlisted)
  allowedSwapper[pool][bob]   = false  (bob is NOT allowlisted)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] == true  ✓
  → swap proceeds — bob bypasses the allowlist
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
