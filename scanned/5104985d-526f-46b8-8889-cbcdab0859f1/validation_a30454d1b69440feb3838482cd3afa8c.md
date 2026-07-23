### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router, not the user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every unprivileged user can bypass the allowlist by routing through the same contract.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [3](#0-2) 

There is no mechanism for the router to forward the originating user's address into the pool's `swap()` call — the pool's external interface accepts only `recipient`, `zeroForOne`, `amountSpecified`, `priceLimitX64`, `callbackData`, and `extensionData`. The pool always derives `sender` from `msg.sender`.

Consequently, the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This creates an irreconcilable dilemma for the pool admin:

- **Router not allowlisted**: allowlisted users cannot use the router at all — broken core functionality.
- **Router allowlisted** (the only way to let allowlisted users use the router): `allowedSwapper[pool][router] == true`, so every user — allowlisted or not — passes the check by routing through the router.

The `DepositAllowlistExtension` does not share this flaw: it ignores `sender` and checks `owner` (the second argument), which the pool always sets to the caller-supplied position owner regardless of who the payer is. [4](#0-3) 

The contest scope explicitly identifies this scenario as the primary validation target for the swap allowlist gate: [5](#0-4) 

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institution-only) that deploys `SwapAllowlistExtension` and allowlists the router loses its entire access-control guarantee for router-mediated swaps. Any unprivileged user can execute swaps against the pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This is a direct policy bypass with fund-impacting consequences: the pool's liquidity providers deposited under the assumption that only vetted counterparties could trade against them.

---

### Likelihood Explanation

High. The router is the primary user-facing swap interface. A pool admin who wants allowlisted users to be able to use the router has no alternative but to allowlist the router address. The bypass is then available to any address with no special privileges, no front-running, and no multi-step setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Signed identity in `extensionData`**: require the user to include a signed proof of their address in `extensionData`; the extension verifies the signature and checks the recovered address against the allowlist.
2. **Separate `originalSender` field in the pool's swap interface**: add an optional `originalSender` parameter to `pool.swap()` that the router populates with `msg.sender` before calling the pool; the extension checks that field instead of `sender`.

The `DepositAllowlistExtension` pattern — checking `owner` (the position owner, not the payer) — is the correct model: it gates the economically attributed actor regardless of which intermediary executes the call.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Pool admin calls setAllowedToSwap(pool, router, true)
   — required so Alice can use the router.
4. Bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — msg.sender inside pool = router.
6. Pool calls _beforeSwap(router, ...).
7. Extension evaluates:
     allowAllSwappers[pool]          → false
     allowedSwapper[pool][router]    → true   ← router is allowlisted
   → check passes, swap executes.
8. Bob receives output tokens despite never being allowlisted.
```

The bypass requires zero privileged access and is reachable through the standard supported periphery path.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
