### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`, and `MetricOmmSimpleRouter` is that immediate caller for all router-mediated swaps, the extension checks the **router's address** rather than the **actual end-user's address**. If the pool admin allowlists the router to enable router-based swaps for their curated users, the gate is fully open to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to each configured extension's `beforeSwap` hook.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is the value the pool received as `msg.sender` — which is the **router contract** when the user enters through `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. [3](#0-2) 

The router calls `pool.swap(params.recipient, ...)` with itself as `msg.sender`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The bypass path:**

A pool admin who wants to allow router-based swaps for their curated users must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether that caller is on the intended allowlist. Any unprivileged user can call `router.exactInputSingle(...)` and the extension will approve the swap because it sees `sender = router`.

Even without the router being explicitly allowlisted, the check is structurally wrong: the identity the extension gates is not the economic actor (the user who controls the funds and receives the output), but the intermediary contract.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a curated, permissioned venue — only specific addresses may trade. Once the router is allowlisted (a necessary step for any curated pool that wants to support the standard periphery UX), the allowlist is completely nullified. Any user can execute swaps in the restricted pool, potentially:

- Draining LP liquidity at oracle-quoted prices that the pool admin intended to offer only to trusted counterparties.
- Bypassing KYC/compliance gates the pool admin put in place.
- Executing swaps that move the pool's bin cursor in ways that harm existing LPs.

This is a **direct loss of LP principal** and a **broken core pool functionality** (the allowlist invariant the pool was deployed with is silently violated).

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a routine operational step for any curated pool that intends to support the standard periphery. No privileged attacker action is needed beyond calling the public `MetricOmmSimpleRouter` functions. The router is a deployed, public contract. Any user who discovers the mischecked identity can exploit it immediately.

---

### Recommendation

The extension must check the **original end-user**, not the immediate `msg.sender` of `pool.swap`. Two sound approaches:

1. **Pass the economic actor explicitly.** Have `MetricOmmPool.swap` accept an explicit `swapper` parameter (separate from `msg.sender`) and forward it to the extension. The router would pass `msg.sender` (the actual user) as `swapper`.

2. **Check `msg.sender` of the extension call against a router allowlist, then verify the user separately.** The extension could require that when `sender` is a known router, the router also attests the real user (e.g., via `extensionData`).

The simplest fix consistent with the existing architecture: change `MetricOmmPool.swap` to accept an explicit `swapper` address (defaulting to `msg.sender` for direct calls), and have the router pass `msg.sender` as that argument. The extension then checks `allowedSwapper[pool][swapper]` against the real user.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is allowed.
3. Pool admin also calls `setAllowedToSwap(pool, address(router), true)` to enable router-based swaps for `alice`.
4. `bob` (not allowlisted) calls `router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob` successfully swaps in a pool he was never authorized to access. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
