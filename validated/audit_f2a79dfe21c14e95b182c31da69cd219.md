### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` equals the **router address**, not the end-user. If the pool admin allowlists the router, every user who routes through it bypasses the per-user restriction entirely. The guard checks the wrong actor and cannot be corrected by the admin without blocking all router-based swaps.

---

### Finding Description

**Step 1 — What the guard checks.**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the value the pool forwarded. [1](#0-0) 

**Step 2 — What the pool actually forwards as `sender`.**

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller of `swap()`) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router, not the end-user
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value verbatim into the extension call: [3](#0-2) 

**Step 3 — How the router calls the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The end-user's address (`msg.sender` of the router call) is stored only in transient storage as the **payer** for the callback; it is never surfaced to the extension. [5](#0-4) 

**Step 4 — The bypass.**

The allowlist check therefore resolves to:

```
allowedSwapper[pool][router_address]
```

If the pool admin allowlists the router (the natural configuration to let users swap through it), the check passes for **every** user who calls the router, regardless of whether that individual user is on the allowlist. The admin has no way to distinguish between different end-users once the router is the `sender`.

**Step 5 — Contrast with `DepositAllowlistExtension`.**

The deposit guard correctly checks `owner` (the position owner explicitly passed to `addLiquidity()`), not `sender` (the operator/payer):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [6](#0-5) 

`MetricOmmPoolLiquidityAdder` passes the actual user as `owner`, so the deposit allowlist enforces per-user control correctly. The swap allowlist has no equivalent "swapper owner" parameter — only `sender` (the router) and `recipient` (the output receiver) — so per-user enforcement is structurally impossible through the router. [7](#0-6) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., whitelisted market makers, KYC'd participants, or protocol-controlled addresses) provides **no effective restriction** for any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted address can execute swaps on the restricted pool by calling the router, bypassing the guard entirely. This breaks the core access-control invariant the extension is designed to enforce, and can result in unauthorized trades draining LP assets at oracle-determined prices.

---

### Likelihood Explanation

The bypass is triggered whenever:
1. A pool deploys `SwapAllowlistExtension` in its `beforeSwap` hook (a production extension explicitly provided for this purpose), and
2. The admin allowlists the router to permit legitimate users to trade through it (the natural and expected configuration).

Both conditions are routine operational choices. No special privileges, malicious setup, or non-standard tokens are required. Any unprivileged user can exploit this by calling `exactInputSingle` or `exactInput` on the router.

---

### Recommendation

The `beforeSwap` extension interface must expose the actual economic actor. Two options:

1. **Short-term**: Change `SwapAllowlistExtension.beforeSwap` to check `recipient` instead of `sender`. While `recipient` is not the payer, it is the address that benefits from the swap output and is not router-controlled.

2. **Correct fix**: Mirror the deposit pattern — add an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity()`), have the router pass `msg.sender` as that value, and have `SwapAllowlistExtension` check it. This gives the admin true per-user control regardless of which intermediary is used.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension in beforeSwap hook.
  - Admin calls setAllowedToSwap(pool, router, true)
    (allowlisting the router so legitimate users can trade through it).
  - Alice (address not individually allowlisted) wants to swap.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: Alice, ...}).
  2. Router calls pool.swap(recipient=Alice, ...) — msg.sender of swap() = router.
  3. Pool calls _beforeSwap(sender=router, recipient=Alice, ...).
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap.
  5. Extension checks allowedSwapper[pool][router] → true → passes.
  6. Swap executes. Alice receives output tokens.

Result:
  Alice, who is not individually allowlisted, successfully swaps on a
  restricted pool. The guard is bypassed via the router intermediary.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
