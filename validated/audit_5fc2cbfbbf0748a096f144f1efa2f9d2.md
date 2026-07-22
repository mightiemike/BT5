### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. However, `MetricOmmPool.swap` passes `msg.sender` (the router contract) as `sender` to the extension hook. When a user swaps through `MetricOmmSimpleRouter`, the extension sees the router's address — not the user's EOA — as the swapper identity. Any user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — here `msg.sender` to the pool is the **router contract**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

4. `ExtensionCalling._beforeSwap` encodes `sender` (= router address) and dispatches to the configured extension: [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the **router**: [3](#0-2) 

The router passes `params.recipient` as the swap recipient but never forwards the originating EOA as a separate argument. The pool has no mechanism to recover the original caller from the router: [4](#0-3) 

**Two broken outcomes result:**

- **Bypass**: If the pool admin allowlists the router address (required for any router-mediated swap to succeed), every user — including those the allowlist was meant to exclude — can swap by calling the router.
- **Lockout**: If the pool admin allowlists individual EOAs but not the router, those allowlisted users cannot swap through the router at all, breaking the primary user-facing path.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties loses that guarantee the moment the public router is used. An unauthorized user calls `exactInputSingle` or `exactInput` on the router; the pool's extension sees the router as the swapper and passes the check if the router is allowlisted. The unauthorized user receives pool output tokens, draining LP value through trades the pool admin explicitly intended to block. This is a direct loss of LP principal through bad-price execution by an unauthorized actor.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` to gate specific counterparties and also needs to support router-mediated swaps is immediately vulnerable. The exploit requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the economically relevant actor, not the intermediary. Two options:

1. **Pass the originating payer through the router**: The router already stores the payer in transient storage for the callback. Extend the `extensionData` payload to carry the originating EOA and have `SwapAllowlistExtension` decode and check it — but this requires the router to cooperate and is forgeable by direct callers.

2. **Check `recipient` instead of (or in addition to) `sender`**: For curated pools where the intent is to restrict who *receives* output, gate on `recipient`. For pools where the intent is to restrict who *initiates* the trade, the extension must be redesigned so the pool enforces identity at the callback level (i.e., the payer, not the caller of `swap`).

The cleanest fix is to have the router encode the originating EOA in a standardized `extensionData` prefix and have the extension verify it against a signature or trusted-forwarder pattern, or to restrict curated pools to direct `pool.swap` calls only (no router support).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so any router swap works at all).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    });
  - Router calls pool.swap(attacker, true, X, ...).
  - Pool calls _beforeSwap(msg.sender=router, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  - Swap executes; attacker receives output tokens.

Result:
  - Attacker, who was never allowlisted as a swapper, successfully trades
    on a curated pool by routing through the public router.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
