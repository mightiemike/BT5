### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks the router's address against the allowlist. If the router is allowlisted (the natural configuration for pools that want to support router-mediated swaps), any unprivileged user can bypass the per-user allowlist entirely by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, `msg.sender` is forwarded verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that same `sender` into the `beforeSwap` call dispatched to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `sender` (the direct pool caller) against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the namespace key and `sender` (the direct caller of `pool.swap()`) as the identity being gated: [3](#0-2) 

**Step 3 — When routing through `MetricOmmSimpleRouter`, the direct caller of `pool.swap()` is the router, not the original user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The original user's address (`msg.sender` of the router call) is stored only in transient storage for the payment callback — it is never forwarded to the pool as the swap `sender`: [4](#0-3) 

The pool therefore sees `msg.sender = router_address`, and the extension checks `allowedSwapper[pool][router_address]`.

**Step 4 — The bypass.**

A pool admin who wants to support router-mediated swaps for allowlisted users must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check at line 37 of `SwapAllowlistExtension` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The original user's identity is never consulted.

The same structural problem applies to the multi-hop `exactInput` path: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce a curated or KYC-gated swap policy is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`) and execute swaps on the restricted pool as long as the router address is allowlisted. The curation invariant — that only approved addresses may trade — is broken, and the pool's LP assets are exposed to unauthorized swap flow. This is a direct loss of the policy value the pool admin intended to enforce, and depending on the pool's purpose (e.g., institutional, compliance-gated), it can constitute a direct financial or regulatory impact on LPs.

---

### Likelihood Explanation

The trigger is unprivileged: any user can call the public router. The precondition — the router being allowlisted — is the natural and expected configuration for any pool that wants to support router-mediated swaps alongside the allowlist. A pool admin who allowlists the router believing it is a trusted intermediary (rather than understanding that the router's address is the identity checked) will unknowingly open the bypass. The `SwapAllowlistExtension` interface and documentation give no indication that router-mediated swaps collapse per-user gating.

---

### Recommendation

The extension must gate on the **original user's identity**, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user address through `extensionData` and verify it with a signature or trusted forwarder pattern.** The router would include the original `msg.sender` in `extensionData`; the extension would verify it against a trusted forwarder registry before consulting the allowlist.

2. **Require allowlisted users to call `pool.swap()` directly** and document that the `SwapAllowlistExtension` is incompatible with router-mediated swaps. The extension should revert if `sender` is a known router/forwarder address.

3. **Redesign the extension to key on `recipient` or a signed claim** rather than `sender`, since `recipient` is the economically relevant output address and is harder to spoof without the recipient's cooperation.

---

### Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Admin allowlists router R: allowedSwapper[P][R] = true
  - Admin allowlists alice: allowedSwapper[P][alice] = true
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, recipient: bob, ...})
  2. Router calls P.swap(bob, ...) — msg.sender in pool = router R
  3. Pool calls E.beforeSwap(sender=R, ...) — msg.sender in extension = P
  4. Extension checks: allowedSwapper[P][R] == true → passes
  5. Swap executes; bob receives output tokens from the restricted pool

Result:
  bob, who is not allowlisted, successfully swaps on a curated pool.
  The allowlist policy is completely bypassed.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
