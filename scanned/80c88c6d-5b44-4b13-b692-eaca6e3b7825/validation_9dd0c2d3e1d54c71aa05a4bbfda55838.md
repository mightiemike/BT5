### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual user. Any unprivileged user can bypass a per-user swap allowlist on a curated pool by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and gates it against the per-pool allowlist: [1](#0-0) 

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

Here `msg.sender` is the pool (correct), and `sender` is whatever the pool passes as the first argument to `beforeSwap` — which is the `msg.sender` of the `pool.swap(...)` call, i.e. the direct caller of the pool.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly: [2](#0-1) 

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

The pool's `msg.sender` is the **router contract**, not the EOA. The pool therefore passes the router's address as `sender` to `beforeSwap`. The extension evaluates `allowedSwapper[pool][router]` — a single shared address — instead of `allowedSwapper[pool][actual_user]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [3](#0-2) 

---

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Scenario A — Router not allowlisted (broken functionality):** A pool admin allowlists specific EOAs. Those EOAs cannot use the router because the extension sees the router address and reverts. Allowlisted LPs and traders are locked out of the standard periphery path, breaking core swap functionality for legitimate users.

**Scenario B — Router allowlisted to unblock legitimate users (allowlist bypass):** The admin adds the router to the allowlist. Now every EOA on the planet can call `exactInputSingle` through the router and the extension passes, because `allowedSwapper[pool][router] == true`. The per-user curation is completely nullified. Any unprivileged user can trade on a pool that was intended to be restricted, draining LP value or executing trades the pool admin explicitly prohibited.

Both outcomes are reachable by any unprivileged caller with no special setup.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and shipped with the protocol. Pool admins who configure `SwapAllowlistExtension` intend to gate individual users; they have no reason to expect that routing through the standard periphery contract changes the identity the extension sees. The bypass requires only a normal router call — no flash loans, no reentrancy, no privileged access.

---

### Recommendation

Pass the **original caller** (the EOA or integrator) through the pool's `swap` call so the extension can gate the economically relevant actor. One approach: add a `sender` parameter to `IMetricOmmPoolActions.swap` that the pool forwards to extensions, and have the router pass `msg.sender` explicitly:

```solidity
// In MetricOmmSimpleRouter.exactInputSingle:
IMetricOmmPoolActions(params.pool).swap(
    msg.sender,        // sender — the actual user
    params.recipient,
    ...
);
```

The extension then checks `allowedSwapper[pool][actual_user]` as intended. Alternatively, the extension can read the payer from a trusted transient context set by the router (analogous to how the liquidity adder stores the payer in transient storage), but the cleanest fix is to thread the real caller through the pool's `swap` signature.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists only `trustedUser`.
swapAllowlist.setAllowedToSwap(pool, trustedUser, true);

// Attacker (not allowlisted) calls the router directly.
// The extension sees sender = address(router), not attacker.
// If router is allowlisted (admin forced to add it for trustedUser to work),
// the check passes and attacker's swap executes on the curated pool.
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Assert: attacker received token1 despite not being on the allowlist.
assertGt(token1.balanceOf(attacker), 0);
``` [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
