### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for specific users inadvertently grants every user the ability to bypass the per-user restriction.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with itself as `msg.sender`: [3](#0-2) 

So the extension sees `sender = router`, not `sender = end user`. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the standard router interface must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **every user** — including those not on the per-user allowlist — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension will pass them through, because the check resolves to the allowlisted router address, not the individual user.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument (the LP position owner), which is explicitly supplied by the caller and can be set to the actual end user even through an intermediary: [4](#0-3) 

This asymmetry means the deposit guard correctly identifies the economic actor, while the swap guard does not.

---

### Impact Explanation

Any user who is **not** on the per-user swap allowlist can bypass the restriction by routing through `MetricOmmSimpleRouter`, provided the router has been allowlisted (a necessary step for any allowlisted user to use the standard router interface). Pools that use `SwapAllowlistExtension` to restrict swap access — for example, to prevent toxic flow from uninformed traders and protect LP value — lose that protection entirely for all router-mediated swaps. LP principal is at risk because the pool receives uninformed order flow it was configured to reject.

---

### Likelihood Explanation

The attack path requires the pool admin to have allowlisted the router. This is a natural and expected configuration: any pool that wants its allowlisted users to interact via the standard periphery must allowlist the router. The `generate_scanned_questions.py` research file explicitly flags this exact scenario as a high-priority validation target: [5](#0-4) 

Once the router is allowlisted, the bypass requires no special privileges — any EOA or contract can call the public router functions.

---

### Recommendation

The extension should identify the end user, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the end user, but this is not reliable for multi-hop or contract-to-contract flows.
2. **Require the router to forward the original caller**: Add an authenticated `swapperOverride` field to `extensionData` that the router populates with `msg.sender` and that the extension verifies against a router registry. The extension then checks `allowedSwapper[pool][swapperOverride]` when a trusted router is the direct caller.

The simplest safe default is to document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and provide a separate per-user gating mechanism that is router-aware.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only intended swapper
  allowedSwapper[pool][router] = true     // admin adds this so alice can use the router

Attack:
  bob (not on allowlist) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  pool.swap(msg.sender=router, ...)
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router)
    → allowedSwapper[pool][router] == true  ✓  (passes)

  bob's swap executes successfully despite not being on the allowlist.
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
