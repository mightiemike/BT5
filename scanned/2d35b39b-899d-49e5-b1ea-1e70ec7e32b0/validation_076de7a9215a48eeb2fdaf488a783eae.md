### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the calling pool) as the pool key and the `sender` argument (which is `msg.sender` from the pool's perspective — i.e., the router) as the swapper key. When a user swaps through `MetricOmmSimpleRouter`, the extension checks whether the **router** is allowlisted, not the **actual user**. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those the admin intended to block — can bypass the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the first argument forwarded by the pool. The pool always passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L343
_afterSwap(
    msg.sender,   // ← same pattern used for _beforeSwap
    recipient,
    ...
)
```

`ExtensionCalling._beforeSwap` encodes that value verbatim:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-L176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` so the pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user — including blocked ones — can swap through the router |
| Do not allowlist the router | Allowlisted users also cannot use the router |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the actual LP owner passed explicitly by the liquidity adder), not the intermediary's address:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The asymmetry between the two extensions confirms that the swap allowlist is checking the wrong actor.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-only, institution-only) with `SwapAllowlistExtension` and allowlists the router to support normal UX inadvertently opens the pool to every public user. Any address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the restricted pool and the extension will pass because it sees the allowlisted router, not the blocked user. The intended access-control boundary is silently nullified.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it.
- A pool admin who wants users to interact via the standard periphery will naturally allowlist the router.
- No special knowledge or privileged access is required; the attacker only needs to call the router with the target pool address.

---

### Recommendation

The extension must resolve the original user's identity rather than the immediate caller. Two options:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (requires a trusted encoding convention).
2. **Extension-side**: Add a `recipient` or `originator` field to the `beforeSwap` interface so the pool can forward the true initiator, or require direct-pool-only swaps for allowlisted pools.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // to enable router UX
  admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(alice_recipient, ...)
  → pool calls extension.beforeSwap(router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; alice bypasses the allowlist
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

**File:** metric-core/contracts/MetricOmmPool.sol (L342-356)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
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
