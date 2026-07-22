### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the originating user. If the pool admin allowlists the router address (a natural action to enable router-mediated swaps for permitted users), every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`** [1](#0-0) 

The extension receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**How the pool populates `sender`** [2](#0-1) 

The pool passes `msg.sender` (its direct caller) as `sender`. For a direct pool call, this is the user. For a router-mediated call, this is the router contract.

**How the router calls the pool** [3](#0-2) 

`exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router, not the originating user. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The broken invariant**

A pool admin who wants to allow specific users to swap via the router has only one option: allowlist the router address itself. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the extension passes for every swap that arrives through the router — regardless of who the originating user is. There is no mechanism in the extension to recover the original user's address from the router call. [5](#0-4) 

The research target for this path explicitly states: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [6](#0-5) 

---

### Impact Explanation

Any non-allowlisted user can swap on a curated pool by routing through `MetricOmmSimpleRouter`. If the pool is designed to restrict access to specific market makers (e.g., to prevent front-running, enforce KYC, or limit manipulation), the bypass allows arbitrary users to extract value from LPs through unfavorable swaps at oracle-derived prices. This is a direct policy bypass on curated pools, which the allowed impact gate classifies as High.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural, non-malicious action: any pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router, since the extension has no other mechanism to permit router-originated swaps. The admin has no way to allowlist specific users *for router paths* — the only granularity available is the direct pool caller. The likelihood is therefore **Medium**: the trigger is a reasonable admin configuration choice, not a malicious one, and the router is the primary user-facing swap entrypoint in the periphery.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two approaches:

1. **Pass the original user via `extensionData`**: Require the router to encode the originating user in `extensionData` and have the extension decode and check it. This requires a coordinated change to the router and extension.

2. **Check `sender` and fall back to a router-forwarded identity**: Add a trusted-router registry to the extension. When `sender` is a registered router, decode the real user from `extensionData` and check that address instead.

The simplest safe default is to document that allowlisting the router grants access to all router users, and provide a separate per-user router-aware extension variant.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-mediated swaps for allowlisted users).
  - Alice (allowlisted directly) and Bob (not allowlisted) both exist.

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData).
     → pool's msg.sender = router.
  3. Pool calls _beforeSwap(router, recipient, ...).
  4. ExtensionCalling._beforeSwap passes sender=router to SwapAllowlistExtension.beforeSwap.
  5. Extension checks allowedSwapper[pool][router] → true → passes.
  6. Bob's swap executes on the curated pool despite not being individually allowlisted.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
``` [1](#0-0) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```

**File:** generate_scanned_questions.py (L659-663)
```python
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
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
