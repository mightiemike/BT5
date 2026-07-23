### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. The allowlist therefore gates the router address, not the actual swapper. Any user can bypass a curated pool's swap policy by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → ExtensionCalling._callExtensionsInOrder(...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this is the router, not the end user
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

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Bypass path:** A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, every user — including those the admin explicitly did not allowlist — can swap freely by calling any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the public router. The allowlist is completely ineffective for router-mediated swaps.

**Broken-functionality path (secondary):** If the admin allowlists individual user addresses but does not allowlist the router, those legitimate users cannot use the router at all, because the hook sees `sender = router` (not allowlisted) and reverts. The only usable path is a direct `pool.swap()` call, which requires the caller to implement `metricOmmSwapCallback` — not a realistic option for EOAs.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only approved addresses may trade. The bypass lets any unpermissioned address execute swaps on a curated pool, draining liquidity at oracle-derived prices. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant. Severity: **High**.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who calls the router on a pool that has allowlisted the router (the only way to make router swaps work at all) triggers the bypass without any special setup. No privileged access, no malicious token, no admin cooperation is required. Likelihood: **High**.

---

### Recommendation

Pass the economically relevant actor — the end user — through the hook, not the intermediary. Two complementary fixes:

1. **In the router:** Store `msg.sender` (the end user) in transient storage alongside the callback context and expose it via a `swapInitiator()` view. Pass it as `extensionData` or as a dedicated field so extensions can read the true originator.

2. **In `SwapAllowlistExtension.beforeSwap`:** Check the `recipient` or a user-supplied identity from `extensionData` rather than `sender` when the `sender` is a known router. Alternatively, document that `sender` is the direct caller of `pool.swap()` and require pool admins to allowlist routers only when they intend open access.

The cleanest fix is for the pool to pass the true initiator as a separate hook argument, analogous to how `DepositAllowlistExtension` correctly gates `owner` (the position beneficiary) rather than `sender` (the intermediary caller).

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured in beforeSwapOrder.
2. Pool admin calls:
       swapExt.setAllowedToSwap(pool, address(router), true)
   (required to enable any router-mediated swap)
3. Attacker (address NOT in allowedSwapper) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
4. pool.swap() fires with msg.sender = router.
5. beforeSwap hook checks allowedSwapper[pool][router] → true → no revert.
6. Attacker's swap executes on the curated pool.
   allowedSwapper[pool][attacker] was never set; the guard was bypassed.
```

**Exact corrupted value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][end_user]`. The wrong identity is checked, so the allowlist invariant is broken for every router-mediated swap. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
