### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the **router contract address** when a swap is routed through `MetricOmmSimpleRouter`. The extension then checks `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][actual_user]`. This means the allowlist gates the intermediary, not the economic actor, producing an exact wrong-actor binding analog to the cross-chain replay class: just as Beanstalk's verifier failed to bind the signature to the correct chain context, this guard fails to bind the check to the correct user identity.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = user.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — pool's `msg.sender` = **router address**.
3. Pool calls `_beforeSwap(msg.sender, recipient, ...)` — passes **router address** as `sender`.
4. `ExtensionCalling._beforeSwap` calls `extension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`. [1](#0-0) [2](#0-1) [3](#0-2) 

The pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, and the extension checks that address against the allowlist. The actual end-user address is never consulted.

This creates an irreconcilable dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

- **If the router is NOT allowlisted:** allowlisted users cannot swap through the standard periphery path at all — the extension reverts on every router-mediated call.
- **If the router IS allowlisted** (the only way to make the pool usable via the router): the allowlist check passes for **every user** who routes through the router, regardless of whether they are individually allowlisted. The per-user curation is completely defeated.

The multihop path compounds this: for hops after the first, the router passes `address(this)` (the router itself) as the payer context, so the same router address appears as `sender` on every hop. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers, or protocol-controlled addresses) is fully bypassable by any unprivileged user who calls the pool through `MetricOmmSimpleRouter`. The attacker pays no extra cost beyond normal swap fees. Any token pair in such a pool is exposed to unrestricted trading, violating the pool admin's curation policy and potentially causing direct financial loss (e.g., LP value leakage to arbitrageurs who should have been excluded, or regulatory/compliance breach with fund-impacting consequences on restricted pools).

This matches the **allowlist bypass** impact gate: a curated pool's allowlist is bypassed through the supported public router path, and the identity check changes from the intended actor to the router contract.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the **standard, documented periphery entry point** for all swaps. Any user who reads the protocol docs will use it.
- No special privileges, flash loans, or unusual conditions are required — a normal `exactInputSingle` call suffices.
- The pool admin has no on-chain mechanism to distinguish "router called by allowlisted user" from "router called by anyone."
- The only mitigation available to the admin (not allowlisting the router) makes the pool unusable via the standard interface, which is not a realistic operational choice.

Likelihood is **high**: the bypass is reachable on every router-mediated swap to any allowlisted pool.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic initiator**, not the intermediary. Two complementary fixes:

1. **In the router:** pass the actual user address as an explicit `sender` field in `extensionData`, and have the extension decode it. This requires a protocol-level convention for the extension payload format.

2. **In the extension (preferred, self-contained):** decode the real initiator from `extensionData` when `sender` is a known router, or require pools using this extension to be called directly (enforce via `onlyPool` + a registry of trusted callers). Alternatively, gate on `tx.origin` as a last resort (with documented caveats), or require the pool to be called only via a wrapper that injects the real user address into `extensionData`.

3. **Architectural fix:** the pool's `_beforeSwap` should pass both `msg.sender` (the immediate caller) and an optional `initiator` field that the router populates with the real user, so extensions can choose which identity to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)  // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true) // REQUIRED for router to work
  
Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) — pool's msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → PASSES
  - Bob's swap executes despite not being individually allowlisted.

Alternatively (if admin does NOT allowlist the router):
  - Alice (allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Extension checks allowedSwapper[pool][router] == false → REVERTS
  - Alice cannot use the standard periphery path at all.
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
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
