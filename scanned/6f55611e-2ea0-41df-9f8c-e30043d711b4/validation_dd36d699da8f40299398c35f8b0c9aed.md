### Title
`SwapAllowlistExtension` gates on the router address instead of the real swapper, allowing any user to bypass a per-user swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, that value is the router's address, not the originating user's address. A pool admin who allowlists the router (the only way to let allowed users trade through the router) simultaneously opens the pool to every user, defeating the per-user curation the extension is supposed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, so the pool's `msg.sender` is the router contract, not the originating EOA: [4](#0-3) 

The consequence is a structural identity collapse: the extension cannot distinguish between different users who route through the same router instance. The only two states are:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps revert, even for allowlisted users |
| Yes | All users bypass the per-user allowlist via the router |

A pool admin who wants allowlisted users to be able to use the router **must** allowlist the router address. Doing so silently grants every user on the network the same swap permission.

---

### Impact Explanation

Any user can bypass a configured `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The attacker pays no special cost beyond normal gas and swap fees. The pool executes the swap at oracle-anchored prices regardless of whether the caller was intended to be gated. Consequences include:

- Unauthorized users trading on a curated/institutional pool at oracle prices, extracting value the pool admin intended to reserve for specific counterparties.
- Adverse selection: the pool absorbs trades from actors the admin explicitly excluded (e.g., high-frequency traders, competitors, or addresses under compliance restrictions).
- Direct loss of LP assets if the pool's oracle pricing was calibrated assuming a controlled, low-toxicity flow.

This matches the allowed impact gate: **broken core pool functionality causing loss of funds** and **admin-boundary break where an unprivileged path bypasses a factory/pool role check**.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production extension in `metric-periphery`, not a test mock.
- `MetricOmmSimpleRouter` is the canonical user-facing swap entry point; any pool admin who wants allowlisted users to trade through the router must allowlist the router.
- The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices.
- The misconfiguration is a natural consequence of the extension's design: there is no way to simultaneously allow router-mediated swaps for specific users and block them for others.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **originating user**, not the pool's `msg.sender`. Two complementary fixes:

1. **Pass the real swapper through the router.** The router already stores the originating user in transient storage (`_getPayer()`). Expose it as a separate argument or encode it in `extensionData` so the extension can read it.

2. **Check `recipient` or a signed identity in `extensionData`.** Alternatively, require the extension to verify a user-supplied, admin-signed allowlist proof in `extensionData`, making the check independent of the call path.

A minimal stopgap: document that allowlisting the router address is equivalent to `setAllowAllSwappers(pool, true)` and enforce this invariant in the admin UI or factory validation.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → router calls pool.swap(bob, true, X, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap receives sender=router
  → allowedSwapper[pool][router] == true  → passes
  → swap executes at oracle price, bob receives output tokens

Result: bob bypasses the per-user allowlist with a single public router call.
Direct call by bob (pool.swap directly) would revert because allowedSwapper[pool][bob] == false.
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
