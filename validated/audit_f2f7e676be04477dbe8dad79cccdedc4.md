### Title
`SwapAllowlistExtension` gates the router address instead of the real end-user, allowing any caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual end-user. If the pool admin allowlists the router so that allowlisted users can reach the pool through the periphery, every unpermissioned address gains the same access, completely defeating the guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the first positional argument forwarded by the pool:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) is used, the call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(params.recipient, ...)   // msg.sender = router
             → extension.beforeSwap(router, ...)
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | **Every** address can bypass the allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The `extensionData` bytes forwarded by the router are ignored by `SwapAllowlistExtension`, so there is no in-band escape hatch.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., for institutional counterparties, KYC-gated users, or whitelisted market makers) and allowlists the router to give those users periphery access inadvertently opens the pool to all callers. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP liquidity without restriction. LP funds are directly at risk from adverse-selection or toxic flow that the allowlist was designed to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. A pool admin who wants allowlisted users to enjoy slippage protection, multi-hop routing, or deadline enforcement must allowlist the router. This is the natural and expected operational step, making the misconfiguration highly probable in any real deployment of a restricted pool.

---

### Recommendation

The extension must verify the **originating** user, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a signature or trusted-forwarder pattern to prevent spoofing).

2. **Check `recipient` instead of `sender`**: If the pool's design intent is to gate who *receives* output tokens, check the `recipient` argument instead. This is not equivalent to gating the payer but may match some use-cases.

3. **Document that the router must never be allowlisted**: Treat the router as an untrusted intermediary and require allowlisted users to call `pool.swap()` directly. This is the safest short-term fix but breaks periphery usability.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is trusted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // so Alice can use the router

Attack:
  - Charlie (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)
      → pool passes msg.sender = router as `sender` to extension
  - Extension checks allowedSwapper[pool][router] → true
  - Charlie's swap executes; allowlist is bypassed.

Result:
  - Charlie trades against LP liquidity that was intended to be restricted.
  - LP principal is exposed to unrestricted toxic flow.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
