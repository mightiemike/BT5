### Title
Swap Allowlist Extension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Curated Pool Access Control — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates access using the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. Because the router is a permissionless public contract, any user can reach an allowlisted pool through it, rendering the per-user allowlist ineffective.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes and dispatches this `sender` to each extension in order: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the router itself `msg.sender` at the pool level: [3](#0-2) 

The `SwapAllowlistExtension.beforeSwap` hook performs an `allowedSwapper` lookup keyed by `(pool, sender)`. In a direct pool call, `sender` is the actual user — correctly gated. In a router-mediated call, `sender` is the router contract address. The router is a public, permissionless contract deployed once for all pools. Therefore:

- If the router address is **not** in the allowlist: every router-mediated swap reverts, breaking the supported periphery path for all users including legitimate ones.
- If the router address **is** added to the allowlist (the only way to restore router functionality): every user on the internet can call `exactInputSingle` and reach the curated pool, because the extension sees only the router's address and approves it.

There is no path through the router that preserves the actual end-user identity for the extension check.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). The bypass allows any unprivileged address to trade on such a pool by routing through `MetricOmmSimpleRouter`, directly violating the pool admin's access-control intent. Trades that should be blocked execute at live oracle prices, exposing LP funds to unintended counterparties and potentially draining the pool if the allowlist was the sole protection against adversarial flow.

Impact: **High** — direct bypass of a configured access-control guard with fund-impacting consequences for LP principals.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps.
- No special setup is required: any EOA can call `exactInputSingle` with the target pool address.
- The bypass is deterministic and requires no oracle manipulation, flash loans, or privileged access.
- The only precondition is that the pool admin has configured `SwapAllowlistExtension` and added the router to the allowlist (or the attacker accepts that router swaps revert until the admin does so).

Likelihood: **High**.

---

### Recommendation

The extension must identify the **economic actor**, not the immediate caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router registry or a signed payload.
2. **Check `tx.origin` only as a last resort** (not recommended for composability).
3. **Preferred**: Redesign the allowlist to key on the `recipient` or require the router to attest the user identity via a signed `extensionData` blob verified inside the extension, with the extension rejecting calls that do not carry a valid attestation.

Additionally, audit `DepositAllowlistExtension.beforeAddLiquidity` for the symmetric issue: `sender` in `addLiquidity` is `MetricOmmPoolLiquidityAdder` when users deposit through the adder, not the position `owner`. [4](#0-3) 

---

### Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured.
  2. Pool admin calls allowlist setter: allowedSwapper[pool][alice] = true.
     Router address is NOT in the allowlist.
  3. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool=targetPool, recipient=bob, zeroForOne=true, amountIn=X, extensionData=""
     ).
  4. Router calls pool.swap(...) — msg.sender at pool = router.
  5. SwapAllowlistExtension.beforeSwap receives sender=router.
  6. allowedSwapper[pool][router] == false → revert.

  Now pool admin adds router to allowlist to restore router functionality:
  7. allowedSwapper[pool][router] = true.
  8. Bob repeats step 3 → swap succeeds.
     Alice's exclusive access is gone; any address can trade.

Expected: Bob's swap should revert because Bob is not allowlisted.
Actual: Bob's swap succeeds because the extension checks the router, not Bob.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
