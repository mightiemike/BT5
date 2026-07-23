### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any non-allowlisted user can bypass the swap allowlist on a curated pool by calling any of the router's `exact*` functions.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the caller of the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
            sender,   // <-- this is msg.sender of pool.swap()
            ...
        )
    )
);
```

In `MetricOmmPool.swap()`, `sender` is always `msg.sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` as `msg.sender`. The pool then passes the **router's address** as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Attack path:**

1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists specific EOAs (e.g., `alice`) via `setAllowedToSwap(pool, alice, true)`.
3. Non-allowlisted user `charlie` cannot call `pool.swap()` directly — the extension reverts.
4. Pool admin also allowlists the router (necessary for allowlisted users to use the router): `setAllowedToSwap(pool, router, true)`.
5. `charlie` calls `router.exactInputSingle(pool, ...)`. The router calls `pool.swap()` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap executes.
6. `charlie` has bypassed the allowlist entirely.

Even if the admin does not allowlist the router, the allowlist is still broken: allowlisted users cannot use the router at all (their EOA address is not what the extension sees), forcing them to call the pool directly. The extension is fundamentally incompatible with the router for per-user access control.

---

### Impact Explanation

A non-allowlisted user can execute swaps on a curated pool that was designed to restrict access to specific participants (e.g., KYC-gated, institutional, or partner-only pools). The unauthorized user can drain LP assets at oracle-derived prices, causing direct loss of LP principal. The allowlist guard — the sole mechanism protecting the pool's curation policy — is silently bypassed.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint. Any user who knows the pool is allowlist-gated can trivially route through the router. No special privileges, flash loans, or multi-step setup are required. The bypass is reachable in a single transaction by any EOA.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the direct caller of `pool.swap()`. Two options:

1. **Pass the original user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`, and the extension decodes and checks it. This requires the router to be trusted to not forge the identity.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is to gate who *receives* output, `recipient` is already forwarded correctly. However, this changes the semantics.

3. **Preferred — gate on `sender` but require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that combine a swap allowlist with a public router).

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)
  admin calls swapExtension.setAllowedToSwap(pool, router, true)  // to allow alice to use router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: charlie,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=charlie, ..., msg.sender=router)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ (passes)
        → swap executes, charlie receives output tokens

Result: charlie bypasses the allowlist and trades on a curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
