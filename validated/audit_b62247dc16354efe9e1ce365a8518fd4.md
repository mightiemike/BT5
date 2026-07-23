Looking at the key files, I can now identify the vulnerability. Let me trace the exact actor binding through the swap allowlist path.

**Key observation in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool (correct), `sender` is the first argument passed by the pool.

**Key observation in `MetricOmmPool.swap`:** [2](#0-1) 

The pool passes `msg.sender` as `sender` to `_beforeSwap`. When the router calls the pool, `msg.sender` = router, so the extension sees the router as the swapper.

**Key observation in `MetricOmmSimpleRouter.exactInputSingle`:** [3](#0-2) 

The router calls `pool.swap(...)` directly — so `msg.sender` to the pool is the router, not the end user.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. Any pool admin who allowlists the router (required for router-mediated swaps to work at all) simultaneously opens the allowlist to every user on-chain, completely defeating the curation policy.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the first argument the pool passes, which is `msg.sender` of the pool's own `swap` call:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when routed, not the end user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap(...)` directly. The pool's `msg.sender` is the router. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → router-mediated swaps fail for everyone, including allowlisted users.
- **Allowlist the router** → any user, allowlisted or not, can bypass the per-user gate by routing through the router.

There is no configuration that simultaneously permits allowlisted users to use the router while blocking non-allowlisted users.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd users, protocol-owned addresses, or whitelisted market makers) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes a real swap against the pool, receiving output tokens and paying input tokens, with no loss of funds to themselves — the pool's curation invariant is broken. This is a **High** impact: broken core pool functionality (allowlist policy) with direct access to pool liquidity by unauthorized actors.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard supported periphery swap router. Any user can call it permissionlessly. No special role, flash loan, or multi-step setup is required. The bypass is a single `exactInputSingle` call. Likelihood is **High**.

### Recommendation

Pass the original end-user address through the swap path so the extension can check it. One approach: add a `swapper` field to `extensionData` that the router populates with `msg.sender` before forwarding, and have the extension decode it. A cleaner approach is to have the pool accept an explicit `swapper` parameter (separate from `msg.sender`) that the router sets to `msg.sender` before calling the pool, and pass that to extension hooks instead of the pool's `msg.sender`. The deposit allowlist already demonstrates the correct pattern: it checks `owner` (the position owner, explicitly passed), not the immediate caller.

### Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     (required so that any allowlisted user can use the router).
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. attacker (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
  5. Router calls pool.swap(attacker, true, X, ...).
  6. Pool calls _beforeSwap(msg.sender=router, ...).
  7. Extension checks allowedSwapper[pool][router] → true → passes.
  8. Swap executes. Attacker receives output tokens.

Result: Non-allowlisted attacker successfully swaps on a curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
