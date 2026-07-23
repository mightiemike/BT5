### Title
`SwapAllowlistExtension` gates the router intermediary instead of the end user, allowing any unprivileged address to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps, every non-allowlisted user can bypass the per-user gate by calling the router. The allowlist invariant is completely broken.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(params.pool).swap(recipient, ...)
          // msg.sender to pool = MetricOmmSimpleRouter address
          → _beforeSwap(msg.sender = router, ...)
              → SwapAllowlistExtension.beforeSwap(sender = router, ...)
                  // checks allowedSwapper[pool][router]  ← router, not the user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making itself `msg.sender`: [4](#0-3) 

**Two broken scenarios result from this mismatch:**

1. **Allowlist bypass (critical path):** Admin allowlists the router address so that users can swap via the router. Because the check is on `sender` = router, *every* user — including non-allowlisted ones — passes the gate when they route through `MetricOmmSimpleRouter`. The per-user allowlist is rendered completely ineffective.

2. **Allowlisted users locked out of the router:** Admin allowlists individual user EOA addresses. Those users cannot swap through the router because `sender` = router address, which is not on the allowlist. They are forced to call the pool directly, losing slippage protection, multi-hop routing, and deadline enforcement.

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` (e.g., a permissioned or KYC-gated pool) is fully open to any caller who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, draining LP value at oracle prices. This is a direct loss of the pool's access-control invariant and constitutes broken core pool functionality with fund-impacting consequences for LPs who deposited under the assumption that only allowlisted counterparties could trade.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who enables the router for their allowlisted pool (the natural operational setup) triggers the bypass. The attacker needs no special privilege — a single call to `exactInputSingle` with any non-zero amount suffices.

---

### Recommendation

The extension must check the **end user**, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — but `recipient` is the output address, not the payer, so this is also wrong for multi-hop paths.
2. **Require callers to pass the real user identity in `extensionData`** and have the router inject `msg.sender` there, then verify it in the extension. This is the only approach that survives router indirection.
3. **Gate at the router level** — the router checks the allowlist before calling the pool, but this is bypassable by calling the pool directly.

The cleanest fix is option 2: the extension reads the attested user address from `extensionData`, and the router always encodes `msg.sender` into `extensionData` before forwarding to the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, address(router), true)
    // admin intends to allow router-mediated swaps

Attack:
  attacker = non-allowlisted EOA
  attacker calls router.exactInputSingle({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: X,
    ...
  })

  // router calls pool.swap(recipient=attacker, ...)
  // pool calls _beforeSwap(sender=router, ...)
  // extension checks allowedSwapper[pool][router] → true
  // swap executes — attacker bypassed the per-user allowlist
```

The `sender` the extension sees is always the router address, never the attacker's address. The allowlist check passes unconditionally for any user routing through the allowlisted router. [5](#0-4) [6](#0-5)

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
