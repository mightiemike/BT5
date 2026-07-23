### Title
Router-mediated swaps bypass `SwapAllowlistExtension` allowlist because `sender` is the router address, not the end user — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller of `pool.swap()`, so `sender` is the **router address**, not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for allowlisted users), every user — including non-allowlisted ones — can bypass the gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The allowlist lookup is `allowedSwapper[pool][router]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all (broken functionality).
- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the gate by routing through the router.

There is no mechanism to distinguish individual end users behind the same router address.

---

### Impact Explanation

A permissioned pool deploying `SwapAllowlistExtension` (e.g., KYC-gated, institutional-only) intends to restrict swaps to a curated set of counterparties. Once the pool admin allowlists the router to enable router-mediated swaps for those counterparties, the allowlist is effectively nullified for all router-originated calls. Any unprivileged user can execute swaps against the pool, extracting value from LPs who expected only trusted counterparties. This is a direct loss of LP principal above Sherlock thresholds for any pool with meaningful TVL.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard periphery swap entry point. Any pool admin who wants allowlisted users to benefit from multi-hop routing or slippage protection will allowlist the router. The bypass requires no special privileges — any EOA can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the pool.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **end user**, not the intermediate router. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap()`**: add the `onlyPool` modifier (currently absent despite the base class requiring it) and check the `sender` parameter only after verifying the caller is a legitimate pool.

2. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (end user) as `extensionData` or as a dedicated field so the extension can recover the true swapper identity. Alternatively, the pool could expose a `swapWithOriginator` entry point that records the originating user in a transient slot readable by extensions.

The simplest immediate fix is to have the router pass the end user's address in `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when `extensionData` is non-empty.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin allowlists the router so allowedUser can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: non-allowlisted attacker routes through the router.
// The extension sees sender = router (allowlisted), not attacker.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// ✓ swap succeeds — allowlist bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
