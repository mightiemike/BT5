### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual end-user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension sees `sender = router address` rather than the actual end-user. Any pool that allowlists the router to support router-mediated swaps simultaneously opens itself to all users, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

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

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call, i.e., whoever called the pool directly.

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) executes a swap, it calls the pool directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [2](#0-1) 

The pool's `msg.sender` is the **router contract**, not the end-user. The pool therefore passes `sender = router address` to `beforeSwap`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable dilemma for any pool that uses `SwapAllowlistExtension`:

1. **Router not allowlisted**: Allowlisted users cannot swap through the router at all — the supported periphery path is broken for them.
2. **Router allowlisted** (to enable router-mediated swaps): Every user on the network can bypass the allowlist by calling the router, since the extension only sees `sender = router` and the router is allowlisted.

The test suite confirms that the pool passes the direct caller as `sender` to the extension — `callers[0]` (a `TestCaller` contract that calls the pool directly) must be allowlisted, not `users[0]` (the human behind it):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [3](#0-2) 

This confirms the extension gates the **direct pool caller**, not the economic actor. The router is always the direct pool caller for router-mediated swaps.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool at oracle-derived prices, draining liquidity that LPs deposited under the assumption that only allowlisted counterparties could trade against them. This is a direct loss of LP principal and fee revenue.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` against any registered pool. No special role, privilege, or precondition is required beyond having the input token. The only prerequisite is that the pool admin has allowlisted the router — a natural and expected action for any pool that intends to support the protocol's own router.

---

### Recommendation

The pool should forward the original end-user identity to the extension rather than its own `msg.sender`. Two complementary fixes:

1. **Pool-level**: Pass an explicit `sender` parameter through the swap call that the router populates with `msg.sender` (the end-user), similar to how `_setNextCallbackContext` already records `msg.sender` for the payment callback. The pool should forward this value to the extension instead of its own `msg.sender`.

2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should accept and check the true originating user. If the pool cannot provide this, the extension could read it from a trusted router's transient storage or require direct pool calls only (documented restriction).

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router address and should document that router-mediated swaps are unsupported.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists only `alice` via setAllowedToSwap(pool, alice, true)
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required for any router-mediated swap to work)

Attack:
  - `charlie` (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls beforeSwap(router, ...) on the extension
  - Extension checks: allowedSwapper[pool][router] → true → PASSES
  - charlie receives output tokens from the pool

Result:
  - charlie bypassed the swap allowlist entirely
  - LP funds are traded against an unauthorized counterparty
  - The allowlist provides zero protection for router-mediated swaps
``` [1](#0-0) [4](#0-3)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
