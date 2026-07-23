### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the actual user. A pool admin who allowlists the router (the natural step to support the standard periphery) inadvertently opens the gate to every user on-chain, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))   // sender = pool's msg.sender
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool's `msg.sender` is the **router**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates an irreconcilable dilemma for any pool admin who wants to use `SwapAllowlistExtension` with the standard periphery:

- **If the router is NOT allowlisted**: every allowlisted user who calls through the router is blocked, breaking the standard swap UX.
- **If the router IS allowlisted** (the natural fix to support the periphery): the check becomes `allowedSwapper[pool][router] == true`, which passes for **every** user who routes through the router, regardless of whether they are individually allowlisted. The allowlist is completely bypassed.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-owned accounts) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity at oracle-derived prices. LP providers who deposited under the assumption of a curated counterparty set suffer unrestricted adverse selection, leading to direct loss of LP principal. This matches the **admin-boundary break** and **broken core pool functionality causing loss of funds** impact categories.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery entry point for swaps. Pool admins who configure `SwapAllowlistExtension` and also want to support the standard router will naturally allowlist the router address. The bypass requires no special privileges, no flash loans, and no complex setup — any user with a standard ERC-20 approval to the router can exploit it in a single transaction. Likelihood is **High** for any pool that combines both components.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the end user — not the intermediary. Two approaches:

1. **Pass the original user through the router**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` (the real user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a protocol-level convention for the extension payload format.

2. **Check `recipient` instead of `sender`** (partial fix): For swap allowlists, the recipient is often the real user. However, this is not always correct (e.g., multi-hop where intermediate recipients are the router itself).

3. **Preferred — dedicated router-aware allowlist**: Introduce a separate `extensionData`-based identity field that the router populates with `msg.sender` before forwarding to the pool, and have the extension verify both the router's identity and the embedded user identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address

Attack:
  1. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: curated_pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
  2. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; attacker receives tokens from the curated pool

Result:
  - attacker, who is not individually allowlisted, successfully swaps
  - SwapAllowlistExtension policy is completely bypassed
  - LP providers in the curated pool are exposed to unrestricted counterparties
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
