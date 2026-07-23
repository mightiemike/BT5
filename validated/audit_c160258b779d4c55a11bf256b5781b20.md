### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is configured by pool admins to restrict which addresses may swap on a curated pool. When a user routes through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender`, not the actual user. The extension's `beforeSwap` hook checks the router's allowlist status, not the real swapper's. If the router is allowlisted (the only way to permit router-mediated swaps), every user on the internet can bypass the allowlist through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

The hook receives `sender` (the first argument) and checks `allowedSwapper[msg.sender][sender]`. `msg.sender` here is the pool (correct), and `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**What the pool passes as `sender`:** [2](#0-1) 

The pool passes `msg.sender` of the `swap` call — i.e., whoever called `pool.swap()` directly.

**What the router passes to the pool:** [3](#0-2) 

The router calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the **router contract**, not the end user. The router stores the real user in transient storage for the payment callback only — it is never forwarded to the pool or the extension.

**Result:** `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`.

This creates two mutually exclusive broken states:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is blocked from using the router; they must call the pool directly |
| Router **allowlisted** | Every user on the internet can bypass the allowlist by routing through the router |

A pool admin who wants to support the standard periphery path must allowlist the router, which silently opens the pool to all users — defeating the entire purpose of the extension.

---

### Impact Explanation

**Direct loss / broken core functionality — High.**

A curated pool using `SwapAllowlistExtension` to restrict trading to a whitelist of counterparties (e.g., KYC'd addresses, protocol-owned bots, or specific market makers) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The pool's LP assets are exposed to unrestricted trading at oracle prices, which the pool admin explicitly intended to prevent. This matches the "allowlist bypass" impact class: disallowed users can trade on a pool that should have rejected them.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the canonical periphery swap path. Any user who discovers the bypass can exploit it immediately with no special setup. The pool admin has no on-chain signal that the router is being used as a bypass vector; the allowlist appears correctly configured from their perspective.

---

### Recommendation

The extension must check the **economically relevant actor**, not the direct caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` against a router registry and fall through to a user-level check**: If `sender` is a known router, decode the real user from `extensionData` and check that address instead.

The deposit allowlist avoids this problem by checking `owner` (the position owner, always the real economic actor) rather than `sender` (the direct caller): [4](#0-3) 

The swap allowlist should adopt the same pattern: gate the address that bears the economic consequence of the swap, not the intermediary that submitted the transaction.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the only way to allow router-mediated swaps.
3. Unauthorized user `attacker` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. Swap executes. `attacker` successfully trades on a pool that should have rejected them. [5](#0-4) [6](#0-5) [3](#0-2)

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
