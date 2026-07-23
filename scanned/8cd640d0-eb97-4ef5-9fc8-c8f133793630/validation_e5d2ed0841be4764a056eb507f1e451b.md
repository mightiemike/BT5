### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (the only way to permit router-mediated swaps) simultaneously grants every user on the network the ability to bypass the per-user allowlist.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routing
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)   // sender = router address
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the end user).

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the second parameter, explicitly supplied by the caller to represent the LP position owner):

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, ...)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit guard is correct because `owner` is the economically relevant actor. The swap guard is broken because `sender` collapses to the router address for all router-mediated swaps.

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Do NOT allowlist the router | Allowlisted users cannot swap through the router; core periphery path is broken |
| DO allowlist the router | Every user on the network can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user allowlist policy.

---

### Impact Explanation

**Direct loss / policy bypass on curated pools.** A pool using `SwapAllowlistExtension` to restrict swaps to KYC'd, whitelisted, or otherwise curated addresses is completely defeated for any user who calls `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. The router is a public, permissionless contract. Any non-allowlisted user can execute swaps against the curated pool by routing through it, draining LP value at oracle-derived prices that the pool admin intended to restrict to specific counterparties. This matches the **High direct loss or curation failure** impact gate.

---

### Likelihood Explanation

**High.** The router is the standard, documented periphery entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router (the normal UX path) will be vulnerable. The admin must allowlist the router to make the pool usable through the periphery, and doing so opens the bypass to all users. No special privileges, flash loans, or unusual conditions are required — a single `exactInputSingle` call suffices.

---

### Recommendation

Replace the `sender` check with the `recipient` parameter (the address that receives swap output) or, better, introduce an explicit `swapper` identity field in the swap call that the router populates with `msg.sender` before calling the pool. The cleanest fix mirrors the deposit pattern: pass the actual end-user address as a named parameter that the extension can check independently of who called the pool.

Alternatively, the extension can decode the actual user from `extensionData` (a bytes field the router already forwards), but this requires a coordinated encoding convention between the router and the extension.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Admin calls setAllowedToSwap(pool, router, true)  // required for router-mediated swaps
  4. bob is NOT allowlisted.

Attack:
  5. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,
       ...
     })
  6. Router calls pool.swap(bob, ...) → pool's msg.sender = router
  7. Pool calls extension.beforeSwap(sender=router, ...)
  8. Extension checks allowedSwapper[pool][router] → true  ✓ (passes)
  9. Swap executes. bob receives tokens from the curated pool.

Expected: revert NotAllowedToSwap (bob is not allowlisted)
Actual:   swap succeeds (router is allowlisted, bob's identity is never checked)
```

**Key code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
