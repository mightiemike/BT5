### Title
`SwapAllowlistExtension` checks router address instead of end-user identity, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — bypasses the guard.

---

### Finding Description

**Pool passes `msg.sender` (the router) as `sender` to the extension:**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
``` [1](#0-0) 

**Extension checks the router address, not the end user:**

In `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
```

Here `msg.sender` is the pool and `sender` is the router. The effective check is `allowedSwapper[pool][router]`. [2](#0-1) 

**Router calls pool with itself as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly; no original-user address is forwarded to the pool or the extension. [3](#0-2) 

**The forced dilemma for pool admins:**

A pool admin who wants to support router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] = true` for every call that arrives through the router — regardless of which end user initiated it. There is no on-chain mechanism to distinguish individual users behind the router, so the allowlist is structurally bypassed for all router paths. [4](#0-3) 

---

### Impact Explanation

**Medium — access-control bypass enabling unauthorized swaps in restricted pools.**

The `SwapAllowlistExtension` is the sole on-chain mechanism for pool admins to restrict who may trade. Once bypassed, any address can swap in a pool that was configured to be private (e.g., KYC-gated, institutional-only, or whitelist-only). Unauthorized swaps drain pool liquidity at oracle price, which is economically fair per swap but violates the pool's intended access model and can expose LPs to counterparties they explicitly excluded.

---

### Likelihood Explanation

**Medium.**

Any pool admin who deploys a `SwapAllowlistExtension` and also wants users to access the pool through the canonical `MetricOmmSimpleRouter` will naturally allowlist the router. This is the expected operational pattern — the router is the primary user-facing entry point documented in the periphery. The bypass is therefore reachable on every allowlisted pool that also permits router access, without any privileged action by the attacker.

---

### Recommendation

The extension must gate on the actual end-user identity, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Add a `swapper` field to the pool's `beforeSwap` signature:** Expose both `sender` (direct caller) and `swapper` (original initiator) so extensions can choose which identity to gate. This is a core interface change but is the cleanest fix.

Until fixed, pool admins should not simultaneously allowlist the router and restrict individual swappers — the two settings are mutually exclusive under the current design.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true);
   (Necessary for any router-mediated swap to succeed.)
3. Pool admin does NOT allowlist Bob:
       allowedSwapper[pool][bob] == false
4. Bob calls router.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient, ...) — msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...).
7. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← router is allowlisted
   → check passes, swap executes.
8. Bob successfully swaps in a pool he was never individually permitted to access.
``` [5](#0-4) [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
