### Title
`SwapAllowlistExtension` per-user allowlist is fully bypassed for all router-mediated swaps when the router is allowlisted — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently opens the pool to **every** user who can call the router, completely defeating the per-user allowlist.

---

### Finding Description

**Invariant broken:** The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." The actual swapper is the EOA or contract that initiated the trade. When that user goes through `MetricOmmSimpleRouter`, the identity that reaches the extension is the router's address, not the user's.

**Root cause — identity substitution in the hook call chain:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = router address when routed
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address against the allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

**The inescapable dilemma for the pool admin:**

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot swap through the router (broken UX) |
| Router **allowlisted** | Every user on the router bypasses the per-user allowlist |

There is no configuration that correctly gates router-mediated swaps by actual user identity. The allowlist invariant is structurally broken for the router path.

This is the direct analog to the external report: just as the `EVMC_DELEGATED` flag is silently stripped when entering a `DELEGATECALL` frame — allowing a guard that depends on that flag to be bypassed — the user's identity is silently replaced by the router's address when entering `pool.swap()` through the router, allowing the allowlist guard to be bypassed.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` (e.g., restricted to KYC'd counterparties, institutional market makers, or a whitelist of trusted addresses) can be accessed by any arbitrary user simply by routing through `MetricOmmSimpleRouter`. If the admin allowlists the router to enable router-mediated swaps for their approved users, the allowlist provides zero protection against unapproved users. Unauthorized swaps can drain LP-owned token reserves at oracle-derived prices, causing direct loss of LP principal.

---

### Likelihood Explanation

Medium. The bypass requires the admin to allowlist the router. However, this is the natural and expected action for any admin who wants their allowlisted users to be able to use the standard periphery router. The admin has no way to achieve "router-mediated swaps for allowlisted users only" — the only options are "block all router swaps" or "allow all router swaps." An admin who chooses the latter (the operationally useful option) unknowingly opens the pool to everyone.

---

### Recommendation

The extension must be able to identify the actual initiating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **`extensionData` attestation:** The router encodes the original `msg.sender` into `extensionData`; the extension verifies the signature or trusts only the router's attestation for router-originated calls, then checks the attested address against the allowlist.

2. **Check `recipient` as a proxy:** For single-hop swaps the recipient is often the actual user; however, this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-aware allowlist:** Maintain a second mapping `allowedRouterSwapper` that the router populates via a dedicated entry point, keeping the direct-call allowlist intact.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin: setAllowedToSwap(pool, alice, true)
          setAllowedToSwap(pool, router, true)   ← required to let alice use the router
3. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(); pool passes msg.sender=router to _beforeSwap.
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. bob's swap executes in the restricted pool.
   allowedSwapper[pool][bob] was never checked.
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
