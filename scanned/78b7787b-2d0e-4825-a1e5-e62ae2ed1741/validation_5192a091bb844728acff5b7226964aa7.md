### Title
Swap Allowlist Checks Router Address Instead of End User, Allowing Any User to Bypass the Gate via the Standard Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual end user. Any pool admin who allowlists the router (required for router-mediated swaps to work at all) simultaneously opens the gate to every user on the network, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender == router
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check therefore asks: **"is the router allowlisted?"** — not "is the end user allowlisted?"

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no forwarding of the original caller:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool sees `msg.sender == router`. The extension sees `sender == router`. The actual caller (`msg.sender` of `exactInputSingle`) is never visible to the extension.

**The pool admin faces an impossible choice:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard router; must call the pool directly |
| **Allowlist the router** | Every user on the network can bypass the allowlist via the router |

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` as `msg.sender == router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified users, protocol-internal actors, or whitelisted market makers). Once the router is allowlisted — which is the only way to let any allowlisted user trade through the standard periphery — the gate is open to the entire public. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the pool's curation policy was designed to prevent. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) expects users to interact through `MetricOmmSimpleRouter` is immediately vulnerable. The router is the standard, documented periphery entry point. A pool admin who reads the README and follows the normal integration path will allowlist the router, unknowingly opening the bypass. The attacker needs no special role, no privileged setup, and no non-standard token — a single call to `exactInputSingle` suffices.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original caller through the router.** The router should forward `msg.sender` to the pool (e.g., via `callbackData` or a dedicated field), and the pool should pass it as a separate `originator` argument to extensions.

2. **Check `originator` in the allowlist extension.** `SwapAllowlistExtension.beforeSwap` should check the originator address when the immediate sender is a known periphery contract, or always check the originator when it is provided.

A simpler short-term mitigation: document that pools using `SwapAllowlistExtension` **must not** allowlist the router, and that allowlisted users must call the pool directly. This is a severe UX restriction that underscores the need for the structural fix.

---

### Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // needed for alice to use router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(router, ...)
  4. Extension checks allowedSwapper[P][router] → true
  5. Swap executes for bob with no revert

Result:
  bob trades on a curated pool that was supposed to block him,
  bypassing the allowlist entirely.
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
