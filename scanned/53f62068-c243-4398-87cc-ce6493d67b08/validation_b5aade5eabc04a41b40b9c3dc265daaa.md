### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always sets `sender = msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` equals the router address, not the actual end user. A pool admin who whitelists the router (the only way to let users swap through it) simultaneously grants every user on-chain the ability to bypass the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that address against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checks router, not user
```

The pool admin has two choices, both broken:

| Admin action | Effect |
|---|---|
| Do **not** whitelist the router | Individually-whitelisted users cannot use the router at all |
| Whitelist the router | **Every** user on-chain can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows whitelisted users to use the router and blocks non-whitelisted users.

---

### Impact Explanation

**Medium.** The `SwapAllowlistExtension` is the production access-control primitive for restricting pool swaps to a defined set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses). When the router is whitelisted, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap in a pool that the admin intended to be restricted. This is a direct admin-boundary break: a pool-level access control configured by the pool admin is fully bypassed by an unprivileged path through the standard periphery router.

---

### Likelihood Explanation

**Medium.** The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that (a) deploys `SwapAllowlistExtension` and (b) needs users to access the pool via the router will encounter this condition. The pool admin must whitelist the router to enable router-based swaps, and doing so silently removes all per-user gating.

---

### Recommendation

The extension must receive the actual end user's address, not the intermediary's. Two approaches:

1. **Pass the real payer through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `recipient` instead of `sender` for swap allowlists**: If the intent is to gate who *receives* output, `recipient` is already the correct field. If the intent is to gate who *pays*, the extension should require the router to attest the payer via signed `extensionData`.

3. **Introduce a dedicated `senderOverride` field in `extensionData`** that the pool validates is signed by the router, allowing the extension to check the true originator.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary for Alice to use the router.
4. Bob (not whitelisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. The check evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite not being individually whitelisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
