### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap(...)` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end-user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router (the only way to let allowlisted users reach the pool via the router) simultaneously opens the gate to every unpermissioned user.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:**

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

`msg.sender` here is the pool (correct — it enforces `onlyPool` semantics). `sender` is whatever the pool passed as the first argument to `_beforeSwap`.

**What the pool passes as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← pool's own msg.sender, i.e. whoever called pool.swap()
    recipient,
    ...
);
```

**What the router passes to the pool:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router stores the real user (`msg.sender`) only in transient storage for the payment callback. It does **not** forward the real user's address to `pool.swap(...)`. The pool therefore receives `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

**The dilemma this creates for pool admins:**

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do NOT allowlist the router | Cannot use the router (reverts) | Correctly blocked |
| Allowlist the router | Can use the router | **Also bypass the allowlist via the router** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**Direct loss of curation policy / unauthorized access to a curated pool.** A pool admin who deploys a `SwapAllowlistExtension`-gated pool intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Any unpermissioned user can bypass this gate by routing through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to support router-mediated swaps for legitimate users), the allowlist is effectively nullified for all router-mediated swaps. This constitutes a broken core pool functionality and a direct admin-boundary break: the pool admin's access-control configuration is bypassed by an unprivileged path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the periphery. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router, which is the natural operational step. The bypass requires no special knowledge, no privileged access, and no front-running — any user can call `exactInputSingle` or `exactOutputSingle` on the router pointing at the curated pool. Likelihood is **High**.

---

### Recommendation

The extension must check the **economically relevant actor** — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the real user through the pool.** The pool's `swap` function could accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` before calling the pool. The extension then checks that field.

2. **Check inside the extension using a forwarded identity.** Alternatively, the router can encode the real user's address inside `extensionData`, and the extension decodes and verifies it (with a signature or trusted-forwarder pattern). This is more complex but avoids a core interface change.

The simplest production fix is option 1: add a `swapper` address to `IMetricOmmPoolActions.swap(...)` that the pool passes to `_beforeSwap` as `sender`, and require the router to populate it with `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)  ← required for Alice to use the router
  - Bob (not allowlisted) holds token0 and has approved the router

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(bob, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true (admin set this for Alice)
  5. Swap executes; Bob receives output tokens from the curated pool
  6. Bob has bypassed the allowlist with zero privilege
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
