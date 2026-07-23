### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` is the router address — not the actual end-user. The allowlist therefore gates the router contract, not the individual trader. Any user can bypass a per-user allowlist by routing through the public router, and any allowlisted user is blocked from using the router unless the router itself is allowlisted (which then opens the gate to everyone).

---

### Finding Description

`MetricOmmPool.swap` (and `simulateSwapAndRevert`) calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on `(msg.sender=pool, sender=caller-of-pool)`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap(...)`, the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address: [4](#0-3) 

The router stores the actual end-user in transient storage for the payment callback but never passes it to the pool as `sender`. The extension has no way to recover the real user.

This produces two mutually exclusive failure modes:

1. **Router not allowlisted** — every allowlisted user who calls through the router is rejected (`NotAllowedToSwap`), making the standard swap path unusable for the pool's intended participants.
2. **Router allowlisted** — every user, allowlisted or not, can swap freely through the router, completely defeating the per-user gate.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, whitelisted protocols, or institutional LPs) cannot enforce that restriction when the public `MetricOmmSimpleRouter` is used. Any unprivileged user can route through the router and trade against the pool's liquidity without being on the allowlist. This constitutes an admin-boundary break: an access control the pool admin explicitly configured is bypassed by an unprivileged path (the public router), allowing unauthorized swaps against pool liquidity.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who discovers the allowlist restriction on direct `pool.swap()` calls can trivially route through the router instead. No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

Pass the original end-user identity through the call chain so the extension can gate on it. Two options:

1. **Preferred — encode the real payer in `extensionData`**: The router already knows `msg.sender` (the real user). It should encode it into `extensionData` before forwarding to the pool, and `SwapAllowlistExtension` should decode and check that address when `extensionData` is present.

2. **Alternative — add a `payer` field to the swap interface**: Extend `IMetricOmmPoolActions.swap` with an explicit `payer` argument (the economic actor), distinct from `recipient`. The pool passes `payer` as `sender` to extensions, and the router sets it to `msg.sender`.

Until fixed, pools that require per-user swap restrictions must not rely on `SwapAllowlistExtension` when the router is accessible.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Admin calls E.setAllowedToSwap(P, alice, true)  — only alice is allowed.
3. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
4. Router calls P.swap(recipient, ...) — pool sees msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[P][router] → false (router not allowlisted).
   → Reverts NotAllowedToSwap.  (Failure mode 1: alice also blocked via router)

7. Admin, wanting router-based swaps to work, calls:
       E.setAllowedToSwap(P, router, true)
8. Bob calls router.exactInputSingle({pool: P, ...}) again.
9. Extension checks allowedSwapper[P][router] → true → swap proceeds.
   → Bob (not allowlisted) successfully swaps. (Failure mode 2: allowlist bypassed)
``` [3](#0-2) [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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
