### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original Swapper, Allowing Any User to Bypass the Per-Pool Swap Gate via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. The pool always passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original EOA. If the pool admin allowlists the router (the only way to support router-mediated swaps for legitimate users), every non-allowlisted user can bypass the gate by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly with no forwarding of the original EOA:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

When this call reaches the pool, `msg.sender` = router contract address. The pool passes that router address as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, **any** user — including those explicitly excluded — can call `router.exactInputSingle()` and the extension will see `sender = router`, pass the check, and execute the swap.

---

### Impact Explanation

The `SwapAllowlistExtension` is the only on-chain mechanism for a pool admin to restrict who may trade against a curated pool. Bypassing it allows unauthorized counterparties to execute swaps, draining LP value at oracle-derived prices. Because the pool settles real token transfers after the extension check passes, every bypassed swap results in direct, irreversible loss of LP principal or owed fees to the pool. The bypass is unconditional once the router is allowlisted: no special privilege, no admin cooperation, and no unusual token behavior is required.

---

### Likelihood Explanation

The bypass requires exactly one precondition: the pool admin must have allowlisted the `MetricOmmSimpleRouter` address. This is the natural and expected configuration for any pool that wants to support the standard periphery swap path for its legitimate users. A pool admin who deploys `SwapAllowlistExtension` to gate specific counterparties and simultaneously wants those counterparties to use the router will inevitably create this configuration. The attacker needs only to call a public router function with a valid pool address and token approval.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **original economic actor**, not the immediate `msg.sender` of `pool.swap()`. Two complementary fixes are possible:

**Option A — Extension reads `tx.origin` (fragile, not recommended for production):** Not recommended because it breaks contract-to-contract flows.

**Option B — Router forwards the original user as a verified parameter:** Add an authenticated `originalSender` field to the swap call or extension data that the pool can verify came from a trusted router. This requires a protocol-level change.

**Option C — Extension checks `recipient` instead of `sender` when `sender` is a known router:** The pool admin can maintain a registry of trusted routers and, when `sender` is a router, fall back to checking `recipient`. This is fragile if `recipient` is also a contract.

**Option D (cleanest) — Pool passes the original payer from transient storage:** The router already stores the original payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`). The pool could expose a hook-level "original payer" that the router writes before calling `swap()`, allowing the extension to check the true initiator. Until this is implemented, the `SwapAllowlistExtension` should document that it **cannot** be used safely on pools that also allowlist any public router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to support alice's router swaps
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  ✓
  6. Swap executes — bob receives tokens at oracle price, LP bears the loss

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds, bob bypasses the allowlist
```

**Relevant code locations:**

- Pool passes `msg.sender` as `sender`: [1](#0-0) 
- Extension checks `sender` (= router, not user): [2](#0-1) 
- Router calls `pool.swap()` without forwarding original EOA: [3](#0-2) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged: [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
