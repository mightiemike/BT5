### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. That argument is `msg.sender` of the pool's `swap` call — which is the `MetricOmmSimpleRouter` when users route through it, not the original user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the gate to every non-allowlisted user, fully bypassing the curation policy.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = msg.sender of pool.swap
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap(...)` directly. At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router, not the originating user.

The pool admin has two choices, both broken:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot swap through the router (broken functionality) |
| Allowlist the router | Every user — including non-allowlisted ones — can bypass the guard by routing through the router |

The second case is the critical one: once the router is in `allowedSwapper[pool][router]`, any address can call `router.exactInputSingle(pool, ...)` and the extension passes, defeating the entire curation policy.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Because the router is a public, permissionless contract, any non-allowlisted address can execute swaps against the pool, draining LP value at oracle-anchored prices that were only intended for the curated set. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant.

---

### Likelihood Explanation

The router is the primary supported swap entrypoint for EOAs. A pool admin who wants their allowlisted users to be able to use the router must add the router to the allowlist — there is no other mechanism. The moment they do, the bypass is live for all users. The trigger requires no special privilege, no flash loan, and no multi-step setup: a single `exactInputSingle` call from any address suffices.

---

### Recommendation

The extension must gate the **original transaction initiator**, not the immediate pool caller. Two complementary fixes:

1. **Pass `tx.origin` as an additional argument** — the pool can forward `tx.origin` alongside `sender` so extensions can check the true initiator. This is the minimal on-chain fix.

2. **Require the router to forward the original user** — the router should pass the original `msg.sender` in `extensionData`, and the extension should decode and check that value. This avoids reliance on `tx.origin` and is compatible with smart-contract wallets if the router is trusted.

The simplest safe fix is to change `SwapAllowlistExtension.beforeSwap` to check `tx.origin` when `sender` is a known router, or to have the pool pass `tx.origin` as a dedicated field in the hook arguments.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the curated user
  allowedSwapper[pool][router] = true     // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

  router calls:
    pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender inside pool = router

  pool calls extension:
    extension.beforeSwap(router, ...)
    // checks allowedSwapper[pool][router] == true  ✓ passes

  bob receives token output — allowlist completely bypassed.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
