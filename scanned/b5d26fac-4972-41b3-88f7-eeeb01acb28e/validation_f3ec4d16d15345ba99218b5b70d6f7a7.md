### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When `MetricOmmSimpleRouter` intermediates a swap, the pool receives the **router** as `msg.sender` and forwards the router address as `sender` to the extension. The extension therefore checks whether the router is allowlisted, not whether the actual end-user is allowlisted. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those explicitly excluded from the per-user allowlist — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses `msg.sender` (the pool) as the mapping key and the forwarded `sender` (the immediate caller of `pool.swap`) as the swapper identity: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap`, the pool's `msg.sender` is the **router contract**, not the end-user: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router_address]`. For any router-mediated swap to succeed at all, the pool admin must add the router to the allowlist. Once the router is allowlisted, the per-user restriction is entirely inoperative: every user who calls the router passes the check regardless of whether their own address is allowlisted.

The inconsistency mirrors the external bug exactly: the allowlist is configured and enforced at the granularity of the **individual swapper**, but the actual runtime check is applied at the coarser granularity of the **immediate `pool.swap` caller** (the router). A user who would be blocked by the fine-grained criterion passes the coarse-grained check by inserting the router as an intermediary.

---

### Impact Explanation

Any user can swap on a pool that has a `SwapAllowlistExtension` configured by routing through `MetricOmmSimpleRouter`. The pool admin's intended access-control boundary — restricting which counterparties may trade — is silently voided. If the allowlist was protecting against specific actors (e.g., MEV searchers, sanctioned addresses, or unauthorized counterparties in a private pool), those actors can trade freely. LP funds are exposed to swap flows the pool designer explicitly intended to block, which can lead to direct LP principal loss through adversarial trading patterns the allowlist was meant to prevent.

---

### Likelihood Explanation

The bypass requires no special privilege. Any user who knows the pool address and the router address can route through `MetricOmmSimpleRouter`. The router is a public, permissionless periphery contract. The only precondition is that the pool admin has allowlisted the router — which is a necessary operational step for any legitimate router-mediated swap to work. The bypass is therefore automatically available to every user the moment the pool is usable via the router.

---

### Recommendation

The extension must verify the **original end-user**, not the immediate `pool.swap` caller. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData`; have `SwapAllowlistExtension` decode and check that address instead of (or in addition to) `sender`.
2. **Recipient-based check**: Gate on the `recipient` argument (second parameter of `beforeSwap`) when the sender is a known router, since the recipient is typically the actual beneficiary. This requires the extension to maintain a registry of trusted routers.

Either way, the extension must not treat the router address as the identity to allowlist-check.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a before-swap hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router-mediated swap.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — Alice is explicitly excluded.
4. Alice calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender == router`.
6. Pool calls `extension.beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → no revert.
8. Alice's swap executes on the restricted pool, bypassing the per-user allowlist entirely. [3](#0-2) [5](#0-4) [1](#0-0)

### Citations

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
