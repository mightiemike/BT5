### Title
`SwapAllowlistExtension` checks the router address as the swapper, not the actual end-user — any user can bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the allowlist checks whether the **router** is permitted — not the actual end-user. If the router is allowlisted, every user on the network can bypass the per-pool swap restriction. If the router is not allowlisted, individually-permitted users cannot swap through the router at all.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool calls `_beforeSwap(sender, recipient, ...)` where `sender` is the pool's own `msg.sender` — i.e., whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter`, the router calls `pool.swap(recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle path
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [2](#0-1) 

So `sender` arriving at the extension is `address(MetricOmmSimpleRouter)`, not the end-user. The allowlist therefore checks the router's address, not the user's address.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position owner supplied explicitly by the router), which is the actual user:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The swap extension has no equivalent `recipient`/`owner` parameter to fall back on — it only receives `sender` (the router) and `recipient` (the output destination), neither of which is the initiating user.

---

### Impact Explanation

Two concrete failure modes, both fund-impacting:

**Mode A — Allowlist fully bypassed (router is allowlisted):**  
Pool admin allowlists the router so that normal users can reach the pool. Because the extension checks the router address, every address on the network can now swap in the pool regardless of individual allowlist status. A pool intended for whitelisted institutional LPs or a private beta becomes open to all, enabling MEV extraction, sandwich attacks, and unauthorized value extraction from the pool's oracle-anchored pricing.

**Mode B — Allowlisted users locked out (router is not allowlisted):**  
Pool admin allowlists specific user addresses but does not allowlist the router. Every swap attempt through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap` even for permitted users, making the pool's swap flow unusable via the standard periphery. Users must call the pool directly, bypassing slippage protection, deadline checks, and multi-hop routing provided by the router.

Both modes break the core pool functionality the allowlist is designed to enforce.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract, not a mock.
- `MetricOmmSimpleRouter` is the primary user-facing swap entry point.
- Any user who calls `exactInputSingle`, `exactInput`, or `exactOutput` through the router triggers the wrong-actor check unconditionally.
- No special privilege, malicious setup, or non-standard token is required.

---

### Recommendation

Pass the actual initiating user through the swap path so the extension can check the correct actor. Two options:

1. **Preferred — use `recipient` as the checked identity for swap allowlists**, since `recipient` is the address that receives the output tokens and is the meaningful economic actor. Change the extension to check `recipient` instead of `sender`.

2. **Alternative — add a `swapper` field to `extensionData`** that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it. This requires the router to sign/encode the real user address in a tamper-evident way.

Additionally, align `SwapAllowlistExtension` with `DepositAllowlistExtension`'s pattern: the deposit extension correctly ignores the intermediary caller and checks the position owner. The swap extension should apply the same principle.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — a natural setup so users can swap through the router.
3. Attacker (not individually allowlisted) calls `router.exactInputSingle(...)` targeting the pool.
4. The pool calls `extension.beforeSwap(address(router), ...)`.
5. The extension checks `allowedSwapper[pool][address(router)]` → `true` → swap proceeds.
6. Attacker successfully swaps in a pool they are not individually permitted to access.

Conversely, if step 2 instead allowlists `address(user)` (not the router), step 3 reverts for the allowlisted user because `allowedSwapper[pool][address(router)]` is `false`. [1](#0-0) [4](#0-3) [3](#0-2)

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
