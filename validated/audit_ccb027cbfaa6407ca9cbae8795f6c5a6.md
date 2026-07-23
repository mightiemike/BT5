### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, any unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards this value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `sender` against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the direct caller of `pool.swap`) as the identity being gated: [3](#0-2) 

**Step 3 — The router calls `pool.swap` directly, so the pool sees `msg.sender = router`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no forwarding of the original user's address: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops): [5](#0-4) [6](#0-5) 

**Result:** The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. The router is a single address; allowlisting it grants every user on the network the ability to swap on the curated pool.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-guarded pool intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers). To allow those users to interact via the standard periphery entry point, the admin must allowlist the router. The moment the router is allowlisted, the guard fails open for the entire public: any address can call `router.exactInputSingle` and the extension will approve the swap because it sees `sender = router`. The allowlist is completely neutralized. Unauthorized users can drain LP value through adversarial swaps on a pool that was supposed to be curated.

---

### Likelihood Explanation

The router (`MetricOmmSimpleRouter`) is the primary user-facing entry point documented and deployed by the protocol. A pool admin who wants to enable router-mediated swaps for their allowlisted users will naturally add the router to the allowlist. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call from any EOA suffices. The only precondition is that the admin has allowlisted the router, which is the expected operational action.

---

### Recommendation

The extension must gate by the economically relevant actor — the human or contract that initiated the swap — not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trust assumption that the router is honest, which is acceptable for a protocol-deployed router.
2. **Check `sender` only for direct pool calls; require the router to be a separate, non-allowlistable entry point:** The extension could distinguish router calls from direct calls and apply different logic, or the router could be redesigned to never be allowlistable.

The `DepositAllowlistExtension` does **not** share this bug: it gates by `owner` (the position owner passed explicitly to `addLiquidity`), which the `MetricOmmPoolLiquidityAdder` forwards correctly regardless of who the payer is. [7](#0-6) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so users can swap via it
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(...) → pool's msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks: allowedSwapper[pool][router] == true  → passes
  - attacker's swap executes on the curated pool

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowlist completely bypassed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
