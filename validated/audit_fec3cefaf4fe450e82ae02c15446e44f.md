### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the curated-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `MetricOmmPool.swap`. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the real user. If a pool admin allowlists the router (the natural step to enable router-based swaps on a curated pool), every unprivileged user can bypass the allowlist by calling any of the router's `exact*` entry points.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` inside the extension is the pool (correct key for the per-pool mapping), but `sender` is whatever the pool passes as the first argument to `_beforeSwap`. The pool always passes its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

So the allowlist lookup becomes `allowedSwapper[pool][router]` — it checks whether the router is allowed, not whether the real end-user is allowed.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the `MetricOmmSimpleRouter` (the natural step to let allowlisted users trade through the standard periphery) inadvertently opens the pool to every user on-chain. Any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool; the extension sees `sender = router`, which is allowlisted, and the swap proceeds. The allowlist policy — intended to restrict trading to KYC'd, institutional, or otherwise vetted counterparties — is completely nullified. Trades execute at live oracle prices against LP capital that was deposited under the assumption of a restricted counterparty set, constituting a direct loss of LP value through adverse selection or policy violation.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is the expected operational step for any curated pool that wants to support the standard periphery UX. The admin has no indication from the contract or documentation that doing so opens the pool to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges.

---

### Recommendation

The extension must check the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` as an extra field in `extensionData` (or a dedicated parameter), and the extension should decode and check that value instead of `sender`.

2. **Alternatively, gate on `recipient` or require direct-pool-only swaps on curated pools.** If the pool admin intends to restrict by identity, the extension should document that router-mediated swaps are not supported and the pool should not allowlist the router.

A minimal fix in `SwapAllowlistExtension` would be to reject any `sender` that is a known router unless the real user is also separately allowlisted, but this requires the extension to know the router address, which is fragile. The cleanest fix is to have the router propagate the originating user address through `extensionData` and have the extension decode it.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin does **not** allowlist `attacker`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Swap executes against LP liquidity; `attacker` receives output tokens.

The allowlist check at line 37–39 of `SwapAllowlistExtension` passes because `sender` is the router, not `attacker`. [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
