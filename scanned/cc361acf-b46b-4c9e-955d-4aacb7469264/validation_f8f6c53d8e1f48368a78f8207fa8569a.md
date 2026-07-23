### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted — not the actual user. Any pool admin who allowlists the router (required for any user to use the standard periphery path) inadvertently grants every unprivileged user the ability to bypass the allowlist entirely.

---

### Finding Description

**Call chain for a router-mediated swap:**

1. User (`charlie`, not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — here `msg.sender = router`.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` passing `sender = router`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. [3](#0-2) 

The extension never sees the original user. It only sees the router address as `sender`.

**The dilemma this creates for pool admins:**

- If the router is **not** allowlisted → no user (including allowlisted ones) can swap through the standard periphery path.
- If the router **is** allowlisted → every user, including non-allowlisted ones, can bypass the guard by routing through `MetricOmmSimpleRouter`.

There is no configuration that allows only specific users to swap through the router. The allowlist is rendered meaningless for the router path.

The router itself confirms it passes no user-identity information to the pool — it simply calls `pool.swap()` as `msg.sender`: [4](#0-3) 

---

### Impact Explanation

**High.** The `SwapAllowlistExtension` is a production safety control designed to restrict trading on curated pools to specific addresses. A non-allowlisted user can trade on any such pool simply by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This completely defeats the purpose of the allowlist, allowing unauthorized parties to extract value from pools that were intended to be restricted (e.g., institutional pools, KYC-gated pools, or pools with specific counterparty requirements). The bypass is direct, requires no special setup, and is reachable by any public user.

---

### Likelihood Explanation

**Medium.** The bypass is only exploitable when the pool admin has allowlisted the router. However, allowlisting the router is the natural and expected action for any pool that wants to support the standard user-facing periphery. Any production deployment that uses `SwapAllowlistExtension` alongside `MetricOmmSimpleRouter` is likely to be vulnerable. The audit targets explicitly flag this exact vector:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4) 

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks this value. This requires the extension to trust the router, which requires the extension to also verify the `sender` is a trusted router.

2. **Check `recipient` instead of `sender`:** For swap allowlists, the economically relevant actor is the one receiving output tokens. The `recipient` argument is already passed to `beforeSwap` (second parameter, currently ignored by `SwapAllowlistExtension`). Checking `recipient` instead of `sender` would correctly gate the beneficiary of the swap.

3. **Preferred — check both `sender` and `recipient`:** Require that both the caller and the recipient are allowlisted, preventing both unauthorized callers and unauthorized beneficiaries.

The `DepositAllowlistExtension` correctly checks `owner` (the position beneficiary) rather than `sender` (the payer/router), which is the right pattern to follow. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension active on beforeSwap
  - Pool admin calls: extension.setAllowedToSwap(pool, alice, true)
  - Pool admin calls: extension.setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - charlie (not allowlisted) calls:
      router.exactInputSingle({
        pool: pool,
        tokenIn: token1,
        tokenOut: token0,
        zeroForOne: false,
        amountIn: 1000,
        ...
      })

Execution trace:
  router.exactInputSingle()
    → pool.swap(msg.sender=router, ...)
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes for charlie

Result:
  charlie successfully swaps on a pool he is not allowlisted for.
  The allowlist guard is bypassed.
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
