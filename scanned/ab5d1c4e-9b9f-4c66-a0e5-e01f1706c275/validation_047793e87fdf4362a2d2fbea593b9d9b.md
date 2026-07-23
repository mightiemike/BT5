### Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is the **immediate caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the allowlist gates the router address, not the originating user. Any unprivileged user can bypass a per-user swap allowlist by calling the public router.

---

### Finding Description

The call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, ...)
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]   ← wrong identity
```

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

When the router is the caller, `sender = router`. The pool admin must allowlist the router contract to permit any router-mediated swap. Once the router is allowlisted, **every user** who calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` passes the check, regardless of whether that individual user was intended to be allowlisted.

The router is a fully public, permissionless contract: [4](#0-3) 

The `generate_scanned_questions.py` research file explicitly identifies this as the critical validation focus for the swap allowlist gate: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers). Once the router is allowlisted — which is required for any user to use the router on that pool — the restriction is completely nullified. Any address can swap against the pool's liquidity at oracle-derived prices, draining LP value in ways the pool admin and LPs did not consent to. This breaks the core access-control invariant and constitutes an admin-boundary break reachable by an unprivileged path.

---

### Likelihood Explanation

Likelihood is high. The router is the standard, documented entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` to restrict access will naturally also allowlist the router so that their permitted users can trade via the normal periphery. The moment they do, the allowlist is bypassed for all users. No special privileges, flash loans, or unusual conditions are required — a single public call to `exactInputSingle` suffices.

---

### Recommendation

Pass the **originating user** through the swap path so the allowlist can gate the correct identity. Two complementary approaches:

1. **Extension-data approach**: Require the router to encode the originating `msg.sender` into `extensionData` and have the extension verify it (with a signature or trusted-forwarder pattern).
2. **Recipient-based approach**: Gate on `recipient` instead of `sender` when the pool is used exclusively with the router, since `recipient` is set by the user and forwarded unchanged.
3. **Direct-pool approach**: Document that allowlisted pools must be accessed directly (not via the router), and add a factory-level flag that prevents the router from calling allowlisted pools.

The cleanest production fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it, so the checked identity is always the originating EOA regardless of intermediary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Alice's swap executes successfully despite not being allowlisted

Result:
  - Alice, an unprivileged and non-allowlisted address, successfully swaps
    on a pool that was intended to be restricted to specific counterparties.
  - The SwapAllowlistExtension guard is completely bypassed.
```

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
