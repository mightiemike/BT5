### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, that argument resolves to the **router's address**, not the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the pool to every caller of the router, bypassing the per-user allowlist entirely.

---

### Finding Description

**The check in the extension:**

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by `MetricOmmPool.swap` via `ExtensionCalling._beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

**What `msg.sender` is in the pool when the router is used:**

`MetricOmmSimpleRouter.exactInputSingle` calls:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is the **router contract**. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Structural asymmetry with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the actual position owner), not `sender` (the liquidity adder contract):

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The deposit path has a dedicated `owner` argument that carries the real user identity through the liquidity adder. The swap path has no equivalent — the real user's address is never forwarded to the extension.

**Two broken invariants:**

1. **Bypass path**: If the pool admin allowlists the router (a natural step so that allowlisted users can use the router), `allowedSwapper[pool][router] == true` passes for *every* caller of the router, regardless of whether the actual user is allowlisted.

2. **Broken-functionality path**: If the pool admin does not allowlist the router, allowlisted users who attempt to swap through the router are blocked, because the extension sees the router's address (not allowlisted) as `sender`.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict swaps to specific users (e.g., KYC'd counterparties, whitelisted institutions) is fully bypassed by any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted. The allowlist — the sole access-control boundary for that pool — provides no protection against router-mediated swaps. Unauthorized users can drain LP assets or execute swaps that the pool admin explicitly intended to block.

---

### Likelihood Explanation

**Medium.** A pool admin who wants allowlisted users to be able to use the router must allowlist the router. This is a natural and expected configuration step. The admin is unlikely to realize that allowlisting the router is equivalent to setting `allowAllSwappers = true` for all router users, because the `DepositAllowlistExtension` does not have this problem and the two extensions appear symmetric in their design.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual user identity, not the intermediary contract. Options:

1. Have the router encode the actual user's address in `extensionData` and have the extension decode and verify it (requires router cooperation and trust in the encoding).
2. Add a dedicated "originator" field to the `beforeSwap` hook arguments so the pool can forward the real user identity separately from `msg.sender`.
3. At minimum, document clearly that allowlisting the router is equivalent to `allowAllSwappers = true` and that per-user allowlisting is only enforceable for direct pool calls, not router-mediated calls.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  extension.setAllowedToSwap(pool, userA, true)   // allowlist userA
  extension.setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it

Attack (userB, not allowlisted):
  router.exactInputSingle({pool: pool, recipient: userB, ...})

  → router calls pool.swap(userB, zeroForOne, amount, priceLimit, "", extensionData)
    msg.sender in pool = router

  → pool calls _beforeSwap(router, userB, ...)

  → extension checks: allowedSwapper[pool][router] == true  ✓

  Result: userB swaps successfully on a pool they are not allowlisted for.
          The allowlist is fully bypassed.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
