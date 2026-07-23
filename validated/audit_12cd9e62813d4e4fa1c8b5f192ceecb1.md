### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Allowlist Bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router address to enable router-based swaps, every user — including non-allowlisted ones — can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:** [1](#0-0) 

The extension checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is the first argument passed by the pool — which is `msg.sender` of the pool's own `swap` call: [2](#0-1) 

So `sender` = whoever called `pool.swap(...)` directly. When the `MetricOmmSimpleRouter` is the caller, `sender` = router address.

**Contrast with `DepositAllowlistExtension`:** [3](#0-2) 

The deposit extension correctly gates on `owner` (the position owner, the economic actor), not `sender` (the payer/caller). The swap extension has no equivalent — it gates on the immediate caller only.

**The bypass path:**

The pool's `addLiquidity` separates `sender` (payer) from `owner` (position owner), and the deposit allowlist correctly checks `owner`. The pool's `swap` has no analogous separation — there is no "end-user" field passed to the extension; only `sender` (= `msg.sender` of the pool call) and `recipient` (output destination).

When a pool admin configures a curated pool with `SwapAllowlistExtension` and needs allowlisted users to be able to use the router, the admin must add the router address to `allowedSwapper`. Once the router is allowlisted, **any address** can call the router and the extension will see `sender = router` (allowlisted) and pass the check, regardless of who the actual end user is. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional partners, or protocol-controlled addresses) can be bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The non-allowlisted user executes a real swap against pool liquidity at oracle prices, receiving output tokens and paying input tokens through the router's callback. The pool's LP positions are exposed to trades from actors the pool admin explicitly intended to exclude. This is a direct policy bypass on curated pools with fund-impacting consequences (unauthorized trading against LP assets).

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address in `SwapAllowlistExtension`. This is a natural and expected operational step: any pool admin who wants allowlisted users to be able to use the router (rather than calling the pool directly) must allowlist the router. Once that step is taken, the bypass is available to any unprivileged user with no special access. The router is a public, permissionless periphery contract. [5](#0-4) 

The `onlyPool` guard in `BaseMetricExtension` only ensures the extension is called by a registered pool — it does not verify the identity of the end user. The allowlist check itself is the only user-identity gate, and it is bound to the wrong actor.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the **economic actor** (the end user whose funds are at stake), not the immediate caller of `pool.swap`. Two approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.

2. **Check `recipient` instead of (or in addition to) `sender`**: For many swap flows the recipient is the end user. However, `recipient` can also be a third-party address, so this is not a complete fix.

3. **Structural fix**: Add an explicit `swapper` field to the pool's `swap` call (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before forwarding to the pool. The pool passes this field to the extension as the authoritative identity to gate on.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Admin calls: swapExt.setAllowedToSwap(pool, router, true)
    (to allow allowlisted users to use the router)
  - Admin calls: swapExt.setAllowedToSwap(pool, alice, true)
    (alice is the only intended authorized swapper)
  - Bob is NOT allowlisted

Attack:
  1. Bob calls MetricOmmSimpleRouter.swap(pool, ...)
  2. Router calls pool.swap(recipient=Bob, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; Bob receives output tokens
  6. Bob has successfully traded on a pool he was explicitly excluded from
``` [6](#0-5) [1](#0-0)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
