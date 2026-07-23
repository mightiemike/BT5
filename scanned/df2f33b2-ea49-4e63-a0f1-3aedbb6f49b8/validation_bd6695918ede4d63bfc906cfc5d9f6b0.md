### Title
SwapAllowlistExtension gates the router address instead of the original user, allowing any unprivileged user to bypass the allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap`. When any user routes through `MetricOmmSimpleRouter`, the pool sees the router as `sender`, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist by going through the router.

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the original user. The allowlist lookup becomes `allowedSwapper[pool][router]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → no user can ever swap through the router on this pool.
- **Allowlist the router** → every user, including those the admin intended to block, can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

There is no configuration that allows specific users to use the router while blocking others. The `generate_scanned_questions.py` research target explicitly identifies this path: [5](#0-4) 

### Impact Explanation

Once the router is allowlisted (the only way to enable router-mediated swaps), any unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and trade against the restricted pool. The allowlist guard — the sole access-control mechanism on the swap path — is fully neutralised. Unauthorized traders can extract LP value through arbitrage or front-running in a pool that was designed to serve only vetted counterparties, constituting a direct loss of LP principal and owed fees above Sherlock thresholds.

### Likelihood Explanation

Medium. The precondition is that the pool admin allowlists the router, which is the natural and expected action whenever the admin wants any user to be able to use the standard periphery. The bypass is then reachable by any unprivileged address with no special setup.

### Recommendation

The extension must check the **original user's identity**, not the intermediary's. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the original caller) into `extensionData` and have `SwapAllowlistExtension` decode and check it. The extension must also verify that `sender` (the direct pool caller) is a trusted router before trusting the decoded identity.
2. **Direct-only policy**: Document clearly that `SwapAllowlistExtension` only gates direct `pool.swap` callers and that router-mediated swaps are always open if the router is allowlisted, so admins do not allowlist the router when they intend per-user gating.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER.
2. Admin calls setAllowedToSwap(pool, router, true)   // must do this to enable router use
3. Alice (not in allowlist) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool:      pool,
           recipient: alice,
           tokenIn:   token0,
           ...
       })
4. Router calls pool.swap(alice, zeroForOne, amount, ...)
   → pool calls _beforeSwap(msg.sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes; Alice trades in the restricted pool.
5. Bob (also not in allowlist) repeats step 3 — same result.
   The allowlist provides zero protection for router-mediated swaps.
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
