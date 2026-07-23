### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Checked Instead of Actual Swapper — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the **router** as `msg.sender`, not the actual user. If the pool admin allowlists the router address — a natural step to enable router-mediated swaps for permitted users — every unpermitted user can bypass the per-user allowlist by routing through the public router contract.

---

### Finding Description

**Root cause — identity mismatch in the hook dispatch chain**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — when the call originates from the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` the pool and extension observe: [4](#0-3) 

**Contrast with the deposit allowlist**

`DepositAllowlistExtension.beforeAddLiquidity` checks the explicit `owner` parameter, which the liquidity adder passes as the actual position owner — not the adder's own address. The swap path has no equivalent explicit-user parameter; the only identity available is `msg.sender` of `swap()`. [5](#0-4) 

**The bypass scenario**

A pool admin who wants to restrict swaps to a set of permitted users (e.g., KYC'd counterparties) will:

1. Set `allowAllSwappers[pool] = false`.
2. Call `setAllowedToSwap(pool, permittedUser, true)` for each permitted user.
3. Call `setAllowedToSwap(pool, router, true)` — believing this is required so permitted users can reach the pool through the standard router.

Step 3 is the trap. The extension now passes any call whose `msg.sender` is the router, regardless of who initiated the router call. An unpermitted user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` and the check resolves to `allowedSwapper[pool][router] == true` → swap succeeds. [6](#0-5) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool. Once bypassed, any unpermitted address can execute swaps against a pool whose admin believed it was access-controlled. Adversarial swappers can extract LP value through repeated one-sided trades at oracle prices, drain a bin, or front-run permitted users — all while the allowlist appears correctly configured. This is a direct loss of LP principal and breaks the core pool invariant that only allowlisted actors may swap.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected operational step: without it, permitted users cannot use the standard router at all. The admin has no in-code warning that allowlisting the router is equivalent to disabling the per-user allowlist for all router callers. The `generate_scanned_questions.py` audit target explicitly flags this path as a concern, confirming it is a realistic production configuration. [7](#0-6) 

---

### Recommendation

1. **Pass the originating user through `extensionData`**: The router should encode `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` should decode and check that address when `extensionData` is non-empty, falling back to `sender` for direct pool calls.

2. **Document the identity semantics**: Clearly state in `SwapAllowlistExtension` that `sender` is `msg.sender` of `pool.swap()`, so pool admins understand that allowlisting any intermediary contract (router, multicall, etc.) grants that contract's entire caller set access.

3. **Align with the deposit allowlist pattern**: Consider adding an explicit `swapper` parameter to the `swap()` call (analogous to `owner` in `addLiquidity`) so the extension can gate on the economically relevant actor rather than the immediate caller.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowAllSwappers[pool] = false
  allowedSwapper[pool][permittedUser] = true
  allowedSwapper[pool][router]        = true   ← admin adds this to enable router access

Attack:
  unpermittedUser calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)          msg.sender = router
  → pool calls _beforeSwap(router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; unpermittedUser receives output tokens

Result:
  unpermittedUser bypasses the per-user allowlist and trades against the restricted pool.
  The pool admin's access-control invariant is silently broken.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
