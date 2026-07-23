### Title
`SwapAllowlistExtension` Swap Guard Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the **router's address** against the allowlist, not the end user's address. Any user can therefore bypass a per-user swap allowlist on a restricted pool by routing through the public router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → router.exactInputSingle(...)
         └─ pool.swap(recipient, ...) [msg.sender = router]
               └─ _beforeSwap(msg.sender=router, ...)
                     └─ extension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address, not the end user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` (the router) is allowlisted:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

For router-mediated swaps to function at all on an allowlisted pool, the pool admin must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** user who routes through it, regardless of whether that user is individually permitted. The per-user allowlist is completely neutralised.

The router itself stores the original user only in transient callback context (`_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, ...)`) for payment purposes; it never forwards the original user's identity to the pool's `swap` call.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., a private OTC pool, a KYC-gated venue, or a protocol-internal pool). Because the extension checks the router's address rather than the end user's address, any unprivileged user can bypass this restriction by calling `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The attacker can execute swaps the pool admin explicitly prohibited, extracting tokens from the pool at oracle-anchored prices without authorisation. This is a direct admin-boundary break: an unprivileged path circumvents a pool-admin-configured access control with fund-impacting consequences.

---

### Likelihood Explanation

Medium. The bypass requires the router to be allowlisted on the pool. This is a natural and expected configuration: a pool admin who wants to support standard user flows through the periphery router must allowlist it. The moment they do, the per-user gate collapses. Any user who knows the pool uses `SwapAllowlistExtension` and that the router is allowlisted can exploit this immediately with no special privileges.

---

### Recommendation

The extension must verify the **original end user**, not the intermediary contract. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension` decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is that the economic beneficiary is `recipient`, the extension can gate on `recipient`. However, this changes the semantics of the allowlist.

3. **Separate the "caller" and "originator" fields in the pool interface**: The pool could accept an explicit `originator` parameter that the router populates with `msg.sender`, and the extension checks `originator` rather than `sender`.

The simplest production fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension` decodes and checks it when `msg.sender` (the pool) is a known pool and `sender` is a known router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only permitted user
  allowedSwapper[pool][router] = true     // router must be allowlisted for normal use

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  pool.swap(recipient=bob, ...) is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  extension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true → passes

  bob receives swap output; allowlist is bypassed.
```

**Relevant code locations:**

- `SwapAllowlistExtension.beforeSwap` checks `sender` (= router): [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` (= router) as `sender`: [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension: [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` without forwarding the original user: [4](#0-3)

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
