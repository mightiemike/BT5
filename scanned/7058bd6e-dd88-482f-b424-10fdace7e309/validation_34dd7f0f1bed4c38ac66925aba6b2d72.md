### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When `MetricOmmSimpleRouter` is the caller, `sender` is the **router's address**, not the originating user. If the router is allowlisted (the only way to permit router-mediated swaps on a curated pool), every user — including those explicitly excluded — can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain:**

```
User
  → MetricOmmSimpleRouter.exactInputSingle()          [line 67-86]
      → pool.swap(recipient, zeroForOne, …, extensionData)  [line 72-80]
          → MetricOmmPool._beforeSwap(msg.sender=router, …) [line 230-240]
              → SwapAllowlistExtension.beforeSwap(sender=router, …) [line 31-41]
                  checks: allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the router) against the per-pool allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap`, `msg.sender` inside the pool is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants to permit router-mediated swaps for any allowlisted user must add the router to the allowlist. Once the router is allowlisted, the check trivially passes for **every** caller of the router, regardless of whether that caller is on the allowlist.

The router itself performs no identity forwarding — it stores only the payer address in transient storage for callback settlement, not for extension gating: [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can:

- Execute swaps on pools whose liquidity and pricing were configured exclusively for allowlisted participants.
- Drain LP-owned assets at oracle-derived prices that the pool admin intended only for trusted counterparties.
- Cause direct loss of LP principal or protocol fees above Sherlock thresholds, matching the **allowlist bypass** and **wrong-actor binding** impact categories.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard production swap entrypoint; most users will interact through it.
- A pool admin who wants any router-mediated swap to succeed must allowlist the router, which simultaneously opens the gate to all users.
- No special privilege, malicious setup, or non-standard token is required — any EOA can call `exactInputSingle` on the router.
- The bypass is unconditional once the router is allowlisted, making it reliably reachable on every curated pool that supports router access.

---

### Recommendation

Pass the **originating user** rather than `msg.sender` (the router) as the `sender` argument to extension hooks. Two concrete approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` pass the originating user as `callbackData` or a dedicated `sender` override field, and have the pool forward it to extensions instead of `msg.sender`.
2. **Extension-side**: `SwapAllowlistExtension` should expose a secondary check path where the router attests the real user identity (e.g., via a signed payload in `extensionData`), and the extension verifies both the router's allowlist status and the attested user's allowlist status.

The simplest safe fix is for the pool to accept an explicit `sender` override from trusted periphery contracts and forward that to hooks, or for the extension to decode the real user from `extensionData` when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  - Pool admin does NOT allowlist bob.

Attack:
  - Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, …})
  - Router calls pool.swap(…) → msg.sender inside pool = router
  - _beforeSwap passes sender=router to SwapAllowlistExtension
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Bob's swap executes on the curated pool despite not being allowlisted.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds, bob extracts value from LP-owned reserves.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

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
