### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. If the pool admin allowlists the router address (the natural operational step to enable router-mediated swaps on a curated pool), every user — including explicitly disallowed ones — can bypass the allowlist by calling through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument the pool passes, which is `msg.sender` of the pool's own `swap()` call: [2](#0-1) 

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

**Router path — original user is invisible to the pool:**

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

```solidity
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

The original user's address is stored only in transient storage for the payment callback — it is **never forwarded to the pool as a parameter**. The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`.

**The two broken states this creates:**

| Pool admin action | Result |
|---|---|
| Allowlists the router (to enable router access) | Every user, including explicitly disallowed ones, can swap through the router — allowlist is fully defeated |
| Does not allowlist the router | Legitimately allowlisted users cannot use the router — core swap path is broken for them |

Neither state is correct. The allowlist is designed to gate by individual swapper identity, but the router collapses all users into a single identity (the router address).

The `DepositAllowlistExtension` does **not** share this flaw because it gates on `owner` (the position owner explicitly passed as a parameter), not on `sender`: [4](#0-3) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap path has no equivalent explicit-user parameter — the pool's `swap()` signature carries no `swapper` argument separate from `msg.sender`.

---

### Impact Explanation

A disallowed user on a curated pool can execute live swaps by routing through `MetricOmmSimpleRouter` whenever the router is allowlisted. The pool receives and settles real token transfers; the allowlist guard that was supposed to prevent this trade passes silently. This is a direct loss of curation control and, depending on the pool's purpose (e.g., KYC-gated, whitelist-only institutional pool), constitutes a high-severity policy bypass with fund-impacting consequences for LPs who deposited under the assumption that only approved counterparties could trade against them.

---

### Likelihood Explanation

The trigger is a normal operational step: a pool admin who deploys a curated pool and also wants to support the standard periphery router will allowlist the router address. This is the expected integration path documented by the protocol. No adversarial setup is required — any user who knows the router address can exploit the bypass immediately after the router is allowlisted.

---

### Recommendation

Pass the original user's address through the swap path so the extension can gate on the correct actor. Two concrete approaches:

1. **Add an explicit `swapper` parameter to `pool.swap()`** alongside `recipient`, and have the router forward `msg.sender` as `swapper`. The extension then checks `allowedSwapper[pool][swapper]`.

2. **Encode the original user in `extensionData`** and have `SwapAllowlistExtension.beforeSwap` decode and verify it (with a signature or trusted-forwarder pattern). This avoids changing the core pool interface.

Either way, the invariant must be: the identity checked by the allowlist is the address that economically initiates and pays for the swap, not the intermediate contract that relays the call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist Alice: allowedSwapper[pool][alice] = false

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: alice, ...})
  2. Router calls pool.swap(alice, zeroForOne, amount, ...)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] = true → PASSES
  5. Swap executes; Alice receives output tokens

Result:
  Alice, an explicitly disallowed swapper, successfully trades against the curated pool.
  The allowlist guard is completely bypassed.
``` [5](#0-4) [6](#0-5) [3](#0-2)

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
