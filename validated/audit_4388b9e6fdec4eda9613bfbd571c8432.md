### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual end-user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those not on the allowlist — can bypass the guard by routing through the public router.

---

### Finding Description

**Configured intent (pool admin):** Restrict swaps to a specific set of addresses by registering them in `allowedSwapper[pool][user]`.

**What the hook actually checks:** [1](#0-0) 

`sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to `msg.sender` of the `swap()` call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

So `msg.sender` inside `MetricOmmPool.swap` is the **router**, not the end-user. The extension receives `sender = router_address` and checks `allowedSwapper[pool][router]`. The actual user's identity is never consulted.

This creates an impossible dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert `NotAllowedToSwap`, even for individually allowlisted users |
| Router **allowlisted** | Every user — allowlisted or not — bypasses the guard by routing through the public router |

The analog to the external bug is exact: the service (extension) receives the intent argument (`sender` = actual user) but silently substitutes a different value (the router address), so the configured guard never enforces the intended policy.

---

### Impact Explanation

The `SwapAllowlistExtension` is a production guard designed to restrict pool access to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or regulated participants). When bypassed:

- Non-allowlisted users can execute swaps against the pool's LP liquidity.
- LPs are exposed to counterparties the pool admin explicitly excluded.
- The pool's core access-control invariant is broken for all router-mediated volume.

This constitutes broken core pool functionality — the allowlist guard is rendered ineffective for the primary public swap path.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard public entry point for swaps. Any user who wants to bypass the allowlist simply calls `exactInputSingle` or `exactInput` on the router instead of calling `pool.swap` directly. No special privileges, no admin access, no malicious setup required — only a standard router call.

---

### Recommendation

The extension must identify the **economic actor**, not the immediate caller. Two options:

1. **Pass the original user through the router:** Have the router store the original `msg.sender` in transient storage and expose it via a callback or a dedicated getter that the extension can read during the hook. The pool would need to forward this value as `sender` rather than its own `msg.sender`.

2. **Check `sender` against the router and fall back to a user-supplied identity:** Require the router to encode the real user in `extensionData`, and have the extension decode and verify it when `sender` is a known router address. This requires trust in the router's encoding.

The simplest correct fix is option 1: the pool's `swap` interface should accept an explicit `sender` override from trusted periphery contracts, or the extension should read the original initiator from transient storage set by the router before the pool call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, alice, true)  // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true) // router must be allowlisted for alice to use it
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → msg.sender inside pool.swap = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. ExtensionCalling dispatches to SwapAllowlistExtension.beforeSwap(sender=router, ...)
  5. Extension checks: allowedSwapper[pool][router] == true  ✓
  6. Swap proceeds — bob successfully swaps despite not being on the allowlist.

Result:
  bob (non-allowlisted) executes a swap against the pool's LP liquidity.
  The allowlist guard is completely bypassed via the public router.
``` [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
