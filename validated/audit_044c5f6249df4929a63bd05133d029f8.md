### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. If the pool admin allowlists the router — a natural configuration for any pool that wants to support router-mediated swaps — every user, including non-allowlisted ones, can bypass the gate.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router is the direct caller of `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The extension has no access to the original user's address; `extensionData` is forwarded opaquely and `SwapAllowlistExtension` ignores it entirely.

This creates an irreconcilable conflict for any pool admin who wants to restrict swaps to specific users while still supporting the router:

| Admin action | Result |
|---|---|
| Allowlist specific users, not the router | Allowlisted users **cannot** swap through the router (broken functionality) |
| Allowlist the router to enable router-mediated swaps | **Every** user can bypass the allowlist through the router |

There is no configuration that achieves "only allowlisted users can swap through the router."

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict access (e.g., to KYC'd counterparties, institutional LPs, or trusted market makers) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The unauthorized swapper trades at oracle-anchored prices against the pool's LP positions. LPs who deposited under the assumption that only vetted counterparties could trade against them are exposed to adverse selection from arbitrary actors — a direct loss of LP principal through unfavorable swap execution that the allowlist was designed to prevent.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router address. This is the expected operational configuration, not an edge case. Once the router is allowlisted, the bypass is available to every user with no special privileges, no flash loan, and no front-running required.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary. Two viable approaches:

1. **Router-side**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.
2. **Extension-side**: Add a router registry to `SwapAllowlistExtension`; when `sender` is a registered router, decode the real user from `extensionData` and check that address instead.

Either approach must be applied consistently across all `exact*` router entry points.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Admin calls: extension.setAllowedToSwap(pool, address(router), true)
   // Admin intends to allow router-mediated swaps for their allowlisted users.
3. charlie (not individually allowlisted) calls:
   router.exactInputSingle({pool: pool, ..., extensionData: ""})
4. Router calls pool.swap() — msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks: allowedSwapper[pool][router] → true → passes.
7. charlie's swap executes against LP positions in the restricted pool.
   // Allowlist invariant broken; unauthorized actor trades against protected LPs.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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
