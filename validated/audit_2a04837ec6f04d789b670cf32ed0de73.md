### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on curated pools to an admin-approved set of addresses. However, when swaps are routed through `MetricOmmSimpleRouter`, the extension receives the **router's address** as `sender` instead of the actual user's address. If the pool admin allowlists the router (which is required for any router-based swap to succeed), every user on the network can bypass the per-user gate by simply routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router itself calls `pool.swap(...)`: [2](#0-1) 

So from the pool's perspective, `msg.sender` is the **router contract**, not the end user. The pool forwards this router address as `sender` into the extension hook.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router address. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For any router-based swap to work on a curated pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, the guard is effectively open to **all users** who call through the router, defeating the purpose of the allowlist entirely.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on the `owner` parameter (the position owner — the true economic actor), not on `sender` (the intermediary): [4](#0-3) 

The asymmetry confirms the swap extension is binding to the wrong actor.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd users, institutional counterparties, or whitelisted bots) loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade against the pool as if they were allowlisted. This is a direct policy bypass with fund-impacting consequences: the pool's liquidity providers deposited under the assumption that only approved counterparties would trade against them.

**Severity: High** — broken core allowlist functionality; any user can bypass a configured access control on a curated pool.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps.
- Any pool admin who wants to support router-based swaps on a curated pool must allowlist the router — the bypass is then immediately active for all users.
- No special permissions, flash loans, or unusual token behavior are required. A standard `exactInputSingle` call suffices.

---

### Recommendation

The `beforeSwap` hook should gate on the **economic actor** — the address that initiated the trade and will receive or pay tokens — not on the intermediary router. Two options:

1. **Pass the original user through extensionData**: The router encodes `msg.sender` (the real user) into `extensionData`, and the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For exact-input swaps the recipient is the user. However, this is also imperfect for multi-hop flows where intermediate recipients are the router itself.

3. **Preferred — dedicated router that forwards the original caller**: The router stores the original `msg.sender` in transient storage and exposes it; the extension reads it via a known interface. This is the cleanest separation of concerns and mirrors how Uniswap v4 handles `msgSender` in hooks.

At minimum, document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that allowlisting the router nullifies per-user gating.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `trustedUser` for swapping
  - Pool admin also allowlists `router` (required for router-based swaps)

Attack:
  1. `attacker` (not in allowlist) calls:
       router.exactInputSingle({pool: pool, ..., recipient: attacker})
  2. Router calls pool.swap(attacker, ...) — msg.sender to pool = router
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true (router was allowlisted)
  5. Swap executes; attacker receives output tokens

Result:
  - `attacker` successfully swapped against a pool that was supposed to
    restrict trading to `trustedUser` only.
  - The allowlist invariant is broken with a standard router call.
``` [5](#0-4) [6](#0-5) [2](#0-1)

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
