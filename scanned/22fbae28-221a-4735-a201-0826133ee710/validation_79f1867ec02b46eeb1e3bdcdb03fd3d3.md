### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` is the router address — not the originating user. If the pool admin allowlists the router (required for any router-mediated swap to work), every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant).
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — here `msg.sender = router`.
3. Pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

4. `ExtensionCalling._beforeSwap` forwards `sender` (= router) to the extension: [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks `allowedSwapper[pool][router]`: [3](#0-2) 

The allowlist lookup is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router. The original user's address is never consulted.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly ignores `sender` and gates on `owner` (the economically relevant actor): [4](#0-3) 

The swap extension has no equivalent `owner`/`recipient` distinction — it uses `sender`, which collapses to the router address on every router-mediated call.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool (e.g., for KYC compliance, institutional access, or regulatory restriction) must allowlist the router for any router-mediated swap to succeed. Once the router is allowlisted, the allowlist offers zero per-user protection: every address on the network can call `MetricOmmSimpleRouter.exactInputSingle()` and the extension will pass because it sees `sender = router`, which is allowlisted. Unauthorized users gain full swap access to a pool that was designed to be restricted, draining LP value through unrestricted arbitrage or front-running that the allowlist was meant to prevent.

This is a broken core pool functionality / admin-boundary break: the pool admin's configured guard is silently bypassed by the standard periphery path.

---

### Likelihood Explanation

Likelihood is high. The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Any pool admin who wants to support router-mediated swaps on an allowlisted pool must allowlist the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any user simply calls the public router. The `generate_scanned_questions.py` audit pivot explicitly flags this exact scenario:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4) 

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should not gate on `sender` (the immediate pool caller). Since swaps have no separate `owner` concept, the extension should either:

1. **Require direct pool calls only** — document that router-mediated swaps are incompatible with the allowlist, and revert if `sender` is not in the allowlist (never allowlist the router itself).
2. **Accept an allowlisted address from `extensionData`** — the router forwards `extensionData` unchanged; the extension can decode a user-supplied address from it and verify it against the allowlist, combined with a signature or other proof that the originating user is the claimed address.
3. **Gate on `recipient`** — if the pool's intended semantic is "only allowlisted addresses may receive output," checking `recipient` instead of `sender` is router-transparent, though it shifts the trust assumption to the recipient field.

Option 1 is the safest and most consistent with the deposit extension's design philosophy.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (required so that any router-mediated swap can pass the gate)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({
        pool: restrictedPool,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: X,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })

Result:
  - pool.swap() is called with msg.sender = router
  - _beforeSwap(sender=router, ...) is dispatched
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives token1
  - The allowlist check on the attacker's address is never performed
``` [6](#0-5) [7](#0-6)

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
