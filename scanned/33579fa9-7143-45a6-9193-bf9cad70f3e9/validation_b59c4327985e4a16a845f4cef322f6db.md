### Title
`SwapAllowlistExtension::beforeSwap` Checks Router (`sender`) Instead of the Actual End-User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the `pool.swap()` call. When users interact through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual end-user. The allowlist is configured per-user by the pool admin, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. This is the direct analog of the external report's wrong-authority check.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct — used as the mapping key), and `sender` is the first argument forwarded by the pool from its own `msg.sender` at the time `pool.swap()` was called.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool as:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
``` [2](#0-1) 

The pool's `msg.sender` is the **router**, so `sender` forwarded to the extension is the router address, not the end-user. The pool admin configures individual user addresses via `setAllowedToSwap(pool, userAddress, true)`, but the extension evaluates `allowedSwapper[pool][routerAddress]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the explicit `owner` parameter (the actual LP), not the direct caller:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The asymmetry confirms the swap extension checks the wrong entity.

---

### Impact Explanation

**Bypass path (Critical):** A pool admin who wants to allow router-based swaps calls `setAllowedToSwap(pool, routerAddress, true)`. This single allowlist entry grants every user — including those the admin explicitly never allowlisted — the ability to swap through the router. The per-user restriction is completely nullified. An attacker who is not on the allowlist can drain liquidity or execute swaps in a pool that was intended to be restricted.

**Broken-functionality path (High):** A pool admin who allowlists individual user addresses finds that those users cannot swap through the router at all (the router is not allowlisted), making the pool's primary swap path unusable for legitimate participants.

Both outcomes have direct fund-impacting consequences: unauthorized swaps against restricted LP positions, or LP assets locked in an unusable pool.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract deployed alongside the router. Any pool that configures this extension and expects per-user gating is affected. The router is the standard swap path for end-users, so the mismatch is triggered on every normal swap interaction. No special attacker capability is required — a standard `exactInputSingle` call suffices.

---

### Recommendation

Replace the `sender` check with the actual end-user. The pool should forward the real initiator separately (analogous to how `addLiquidity` takes an explicit `owner`), or the extension should check `recipient` if that carries the user identity, or the router should pass the real user via `extensionData`. The minimal fix is to align the checked address with the entity the allowlist is configured for:

```diff
- if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ // sender here is msg.sender of pool.swap() = router; use recipient or
+ // a user address forwarded through extensionData instead
+ if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

A more robust fix is to have the router encode the real `msg.sender` into `extensionData` and have the extension decode it, with the pool verifying the binding via the callback context (already stored in transient storage).

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, routerAddress, true)` — intending to allow router-based swaps.
3. Attacker (address never allowlisted) calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker successfully swaps in a pool that was supposed to restrict them, receiving tokens from LP positions.

Alternatively:
1. Pool admin calls `setAllowedToSwap(pool, userAddress, true)` for a legitimate user.
2. Legitimate user calls `router.exactInputSingle(...)`.
3. Extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert.
4. Legitimate user is permanently blocked from the standard swap path despite being allowlisted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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
