### Title
`SwapAllowlistExtension` checks the router's address instead of the end-user's address, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router contract is `msg.sender` of `pool.swap()`, not the end user. The allowlist therefore checks the router's address, not the user's. If the router is allowlisted (which is required for any user to swap through it), the per-user restriction is completely bypassed for all callers.

---

### Finding Description

**Root cause — wrong identity checked in the guard**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses it as the identity to check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the first argument forwarded by the pool's `_beforeSwap` internal call, which is set to `msg.sender` of `pool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end user
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

**How the router breaks the guard**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The original user's address (`msg.sender` of `exactInputSingle`) is stored only in the callback context for token settlement. It is **never forwarded** to the pool's `swap` call. The pool sees `msg.sender = router`, so `sender = router` is what the extension checks.

**Contrast with `DepositAllowlistExtension`**

The deposit allowlist correctly checks the `owner` parameter (the position owner explicitly passed by the caller), not `sender` (the direct caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This works correctly because `owner` is the intended identity regardless of who the intermediary caller is. The swap extension has no equivalent mechanism — it only has `sender` (the direct caller) and `recipient` (the output destination), neither of which reliably identifies the end user when a router is in the path.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified traders, institutional counterparties, or whitelisted market makers) faces a binary failure:

1. **Router not allowlisted**: No user can swap through `MetricOmmSimpleRouter` even if they are individually allowlisted, breaking the standard swap UX entirely.
2. **Router allowlisted** (the only way to enable router-based swaps): Every address on the network can call `exactInputSingle` or `exactInput` through the router and execute swaps against the restricted pool, completely defeating the allowlist.

In either case the guard is misapplied. In case 2, unauthorized users gain direct access to pool liquidity — they can drain bins at oracle-derived prices, extract value from LP positions, and interact with a pool that was explicitly configured to exclude them. This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path (the router) bypasses a configured access control.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract, not a test mock. Any pool that configures it as a `beforeSwap` extension and expects per-user gating is affected. The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle`. The only precondition is that the router is allowlisted, which is a natural operational step any admin would take to enable normal usage.

---

### Recommendation

The `beforeSwap` extension hook must receive the true end-user identity. Two options:

**Option A — Pass the original user through `extensionData`**: Require callers (routers) to encode the originating user address in `extensionData`, and have `SwapAllowlistExtension` decode and check it. This is opt-in and requires router cooperation.

**Option B — Add a dedicated `swapOriginator` field to the `beforeSwap` signature**: Extend `IMetricOmmExtensions.beforeSwap` with an explicit originator address that the pool populates from a trusted source (e.g., a router that sets it via a pre-swap call or transient storage). This mirrors how `DepositAllowlistExtension` uses `owner` rather than `sender`.

**Option C — Check `sender` AND require the router to be non-allowlistable**: Document that `SwapAllowlistExtension` only works when users call the pool directly (no router), and enforce this at the extension level by rejecting known router addresses as `sender`.

The simplest safe fix consistent with the existing deposit pattern is Option A or B: the extension should check an identity that represents the economic actor, not the contract that happens to be the immediate caller.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension as beforeSwap extension
  - Admin allowlists only address ALICE: allowedSwapper[P][ALICE] = true
  - Admin also allowlists the router (required for ALICE to use it): allowedSwapper[P][router] = true

Attack:
  - BOB (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  - Router calls P.swap(recipient=BOB, ...) → msg.sender of pool.swap() = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[P][router] == true → PASSES
  - BOB's swap executes successfully against the restricted pool

Result:
  - BOB, who was never allowlisted, executes a swap against a pool that was
    configured to exclude him, receiving pool tokens at oracle-derived prices.
  - The SwapAllowlistExtension guard is completely bypassed via the router.
```

The `FullMetricExtensionTest` confirms this identity: `test_allowedSwapSucceeds` allowlists `address(callers[0])` (the `TestCaller` contract, the direct pool caller) — not `users[0]` (the end user) — demonstrating that the check is on the intermediary, not the economic actor. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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
