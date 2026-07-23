### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` is keyed on the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted rather than the actual end user. Any unprivileged caller can bypass a pool's swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

The `IMetricOmmExtensions.beforeSwap` interface receives `sender` as its first argument: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` performs its allowlist lookup keyed on `(pool, sender)`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, not the originating EOA. The extension therefore evaluates whether the router is allowlisted, not whether the actual trader is allowlisted. Because `MetricOmmSimpleRouter` is a public, permissionless contract, any user who is not on the allowlist can route through it and have the check pass (or fail for allowlisted users who cannot use the router at all).

The research file explicitly identifies this as the critical validation focus for this target: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) provides **zero effective access control** for router-mediated swaps. Any non-allowlisted address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the public router and execute swaps against the restricted pool. This allows unauthorized parties to drain LP assets, extract value at oracle-quoted prices, and interact with pools that were explicitly designed to exclude them — a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

Likelihood is **high**. The `MetricOmmSimpleRouter` is a public, deployed periphery contract. No special privilege, token balance, or setup is required beyond knowing the pool address. The bypass is a single function call. Any actor who is denied direct pool access will immediately discover that routing through the router succeeds.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **originating user**, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`**: For most swap use cases the recipient is the economic beneficiary; allowlisting on recipient closes the router bypass.
2. **Require `sender == recipient` or pass the real user through `extensionData`**: The router can encode `msg.sender` into `extensionData` and the extension can decode and verify it, with the router signing or the pool enforcing that only trusted routers may forward identity claims.
3. **Allowlist the router separately and require it to attest the caller**: The router exposes the real caller in `extensionData`; the extension trusts only known routers and reads the attested address from the payload.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension configured.
   allowedSwapper[P][alice] = true   (alice is the only permitted swapper)
   allowedSwapper[P][router] = false (router is not explicitly listed)

2. Bob (not allowlisted) calls:
   MetricOmmSimpleRouter.exactInputSingle({
       pool: P,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
   })

3. Router calls pool.swap(recipient=bob, ...) with msg.sender = router.

4. Pool calls _beforeSwap(sender=router, recipient=bob, ...).

5. SwapAllowlistExtension.beforeSwap checks allowedSwapper[P][router].
   If allowAll[P] == true OR allowedSwapper[P][router] == true → swap proceeds.
   Bob receives tokens from a pool he was never supposed to access.

6. Alternatively, if the pool admin allowlisted the router to let alice use it,
   every user on the internet can now swap against the "restricted" pool.
```

The root cause is that `sender` in the hook is the router's address, not the EOA that initiated the transaction, so the allowlist invariant — "only approved addresses may swap" — is broken for every router-mediated path. [6](#0-5) [7](#0-6) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
