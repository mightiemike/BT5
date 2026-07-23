### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender = router`. If the router is allowlisted (which is required for any allowlisted user to swap through the router), every user—including non-allowlisted ones—can bypass the gate by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the real user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routed
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check passes whenever the router is in `allowedSwapper[pool]`.

**The invariant break:** The extension cannot simultaneously (a) allow allowlisted users to swap through the router and (b) block non-allowlisted users from doing the same. If the router is allowlisted (the only way for any user to use the router on a gated pool), the allowlist is completely bypassed for every user.

---

### Impact Explanation

Any non-allowlisted user can execute swaps on a pool that is supposed to be restricted to a specific set of addresses. This directly breaks the core access-control invariant the extension is designed to enforce. Depending on the pool's purpose (e.g., institutional-only pools, KYC-gated pools, or pools with restricted counterparties), this allows unauthorized parties to drain or manipulate pool liquidity, extract value at oracle-driven prices, or interact with pools they are explicitly excluded from.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. The only precondition is that the router is allowlisted on the pool—which is a necessary operational step for any allowlisted user to use the router at all. A pool admin who wants to allow legitimate users to swap through the router must allowlist the router, which simultaneously opens the gate to everyone. This is a normal, expected configuration, not an edge case.

---

### Recommendation

The extension must gate the actual end-user, not the intermediary router. Two approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the extension to trust the router's encoding, which introduces a separate trust assumption.

2. **Check `sender` only for direct pool calls; require the router to be excluded from allowlisting**: Document that the router must never be allowlisted and that allowlisted users must call the pool directly. This is operationally fragile.

3. **Preferred — router-level identity forwarding with extension verification**: The router stores the real user in transient storage (it already does this for the callback payer via `_setNextCallbackContext`). Extend this to pass the real user in `extensionData` and have `SwapAllowlistExtension` decode and check it when `sender` is a known router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(sender=router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Bob's swap executes successfully despite never being allowlisted.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
