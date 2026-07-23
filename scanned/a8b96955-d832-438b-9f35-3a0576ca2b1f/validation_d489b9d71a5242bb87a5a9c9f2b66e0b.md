### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Any User Bypasses Per-User Swap Gate via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address — not the end user's. If the pool admin allowlists the router (required for any router-mediated swap to work), every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              msg.sender = router
              → _beforeSwap(sender = msg.sender = router, ...)
                   → extension.beforeSwap(sender = router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the end user
```

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards this verbatim to the extension. `SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` = pool and `sender` = router (not the end user).

For the router to be usable at all on an allowlisted pool, the admin must add the router to `allowedSwapper[pool][router]`. Once that entry exists, every user who calls any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router passes the check — because the extension sees only the router's address, regardless of who initiated the call.

There is no mechanism in the router to forward the originating user's address into the `sender` slot; the router passes `""` as `callbackData` for exact-input paths and the extension ignores `extensionData`.

---

### Impact Explanation

A pool admin deploys a private pool (e.g., for institutional LPs or a whitelist-only market) and configures `SwapAllowlistExtension` to restrict swaps to a small set of approved counterparties. To let those approved users trade via the standard router UI, the admin adds the router to the allowlist. From that moment, any unpermissioned address can call `router.exactInputSingle(pool, ...)` and execute swaps against the pool's LP positions. The allowlist provides zero protection against router-mediated access. LP funds are exposed to the full public swap surface the admin intended to close.

Impact category: **broken core pool functionality / admin-boundary break** — the access-control invariant the pool admin configured is silently voided for all router paths.

---

### Likelihood Explanation

- The router is the primary user-facing entry point documented and deployed by the protocol.
- Any pool admin who wants their allowlisted users to trade via the router must add the router to the allowlist, triggering the bypass automatically.
- No special permissions, flash loans, or unusual token behavior are required — a plain `exactInputSingle` call suffices.
- The bypass is permanent once the router is allowlisted; it cannot be undone without removing the router from the allowlist (which also blocks all legitimate router users).

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two viable fixes:

1. **Pass the true initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. This requires a protocol-level convention.

2. **Check `recipient` instead of (or in addition to) `sender`**: For single-hop swaps the recipient is the end user. This is imperfect for multi-hop paths where intermediate recipients are the router itself.

3. **Preferred — dedicated router that forwards the initiator**: The router stores the originating `msg.sender` in transient storage and exposes it; the extension reads it via a trusted router interface. This is the cleanest separation and mirrors how Uniswap v4 handles hook-level identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended user)
  - allowedSwapper[pool][router] = true  (admin adds router so alice can use the UI)

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({
      pool:      pool,
      recipient: charlie,
      tokenIn:   token0,
      amountIn:  X,
      ...
    })

  router calls pool.swap(charlie, zeroForOne, X, ...)
    msg.sender to pool = router

  pool calls extension.beforeSwap(sender=router, ...)
    check: allowedSwapper[pool][router] == true  ✓

  charlie's swap executes successfully.
  The allowlist is bypassed.
```

**Relevant code locations:**

`MetricOmmPool.swap` passes `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `sender` (= router, not end user): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` as `msg.sender = router`: [4](#0-3) 

`exactInput` multi-hop path — same issue, router is always the direct caller of each pool: [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
