### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which is always `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router (the only way to make router-mediated swaps work on a curated pool) inadvertently opens the pool to every caller, because the extension then passes for any address that routes through the allowlisted router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist, keyed by `msg.sender` (the pool):

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
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap` directly, making the router the `msg.sender` seen by the pool:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The router stores the original `msg.sender` only in transient storage for the payment callback; it is never forwarded to the pool's `swap` call or to any extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all; they must call the pool directly |
| **Allowlist the router** | Every address on the network can bypass the allowlist by routing through the router |

The second branch is the exploitable path. The admin's intent — "let my allowlisted users swap through the router" — is structurally impossible to express with the current extension design, so the only practical fix is to allowlist the router, which silently removes all curation.

---

### Impact Explanation

A curated pool (e.g., a KYC-gated or institution-only pool) that has allowlisted the router loses all swap-side access control. Any unprivileged address can call `router.exactInputSingle` (or any other router entry point) targeting the pool and execute swaps that the allowlist was meant to block. This constitutes a direct bypass of a configured security boundary with fund-impacting consequences: non-allowlisted users can drain liquidity at oracle-derived prices, extract value from the pool, or front-run allowlisted participants.

---

### Likelihood Explanation

The trigger is a single unprivileged call to any public router function. The precondition — the router being allowlisted — is the natural and expected administrative action for any pool that wants to support router-mediated swaps alongside its allowlist. There is no on-chain signal that distinguishes "router allowlisted intentionally to open the pool" from "router allowlisted to enable curated router access," so the misconfiguration is easy to introduce and hard to detect. Likelihood is **High** for any curated pool that also supports the periphery router.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **originating user**, not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Extension-side**: Accept an optional `bytes calldata extensionData` field that the router populates with the original `msg.sender` (signed or trusted). The extension verifies that field instead of (or in addition to) `sender`.

2. **Router-side**: Forward the original caller's identity through a dedicated field in the extension payload so the extension can reconstruct and verify the true economic actor.

Until fixed, pool admins should **not** allowlist the router on curated pools and should require allowlisted users to call `pool.swap` directly.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so that allowlisted users can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// allowedUser is individually allowlisted
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: bannedUser routes through the router
vm.prank(bannedUser);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        recipient:        bannedUser,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// ✓ swap succeeds — bannedUser bypassed the allowlist
// Extension saw allowedSwapper[pool][router] == true, not allowedSwapper[pool][bannedUser]
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the guard passes for `bannedUser` without any check against `bannedUser`'s own allowlist entry. [4](#0-3) [5](#0-4) [1](#0-0)

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
