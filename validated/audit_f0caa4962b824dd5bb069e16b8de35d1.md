### Title
SwapAllowlistExtension checks router address instead of actual end-user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension` is designed to gate pool swaps to a configured set of permitted addresses. However, when swaps are routed through `MetricOmmSimpleRouter`, the `sender` argument the extension receives is the **router's address**, not the actual end-user. This means the allowlist guard is misapplied: allowlisting the router grants unrestricted access to every user, while not allowlisting the router blocks all router-mediated swaps even for individually allowlisted users.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` then calls each extension's `beforeSwap(sender, ...)` with that value: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` uses the received `sender` to look up the allowlist, keyed by `msg.sender` (the pool) and `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router swap entry point), the router calls `pool.swap(...)` directly. Inside the pool, `msg.sender` is the **router**, so `sender` delivered to the extension is the router address: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`. This creates two mutually exclusive failure modes:

1. **Router not allowlisted**: Every individually allowlisted user is blocked from using the router, even though they are permitted by the pool admin's intent.
2. **Router allowlisted** (required for any router-mediated swap to work): Every user — including those the pool admin explicitly did not allowlist — can bypass the guard by routing through the public `MetricOmmSimpleRouter`.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, institutional partners) loses that protection entirely for any user who routes through the public router. The attacker does not need any special privilege: they simply call `exactInputSingle` or `exactInput` on the router with the target pool. The pool receives the swap with `sender = router`, the extension approves it (because the router must be allowlisted for legitimate users to trade), and the disallowed user executes a trade that the pool admin intended to block.

This is a direct broken-core-functionality impact: the pool's primary access-control mechanism fails silently for the primary user-facing entry point.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap interface documented and deployed for the protocol. Any pool that uses `SwapAllowlistExtension` and also expects users to trade through the router must allowlist the router, which immediately opens the bypass to all users. The trigger requires no privileged access, no special token behavior, and no multi-transaction setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that value. This requires a trusted router binding (the extension must verify the caller is a known router before trusting the payload).

2. **Check both router and originating user**: The pool or router exposes the original initiator via a transient-storage slot (already used for callback context), and the extension reads that slot instead of relying on the `sender` argument.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, router, true)` — required for any router swap to work.
3. Admin calls `setAllowedToSwap(pool, alice, true)` and intentionally does **not** allowlist `bob`.
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. `bob`'s swap executes successfully, bypassing the per-user allowlist. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
