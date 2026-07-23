### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the originating user. If the pool admin allowlists the router (required for router-mediated swaps to work at all), every unprivileged user can bypass the swap allowlist by routing through the router.

---

### Finding Description

**Call chain through the router:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router_address` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][original_user]`.

The check in `SwapAllowlistExtension`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the router (wrong — it should be the originating user).

The pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- router address when called via router
    recipient,
    ...
);
```

**The inescapable dilemma for pool admins:**

| Router allowlist state | Effect |
|---|---|
| Router NOT allowlisted | Legitimate allowlisted users cannot use the router at all |
| Router IS allowlisted | Every user, including non-allowlisted ones, can bypass the gate |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of counterparties (e.g., KYC'd users, institutional partners, or specific protocols). Once the router is allowlisted — which is necessary for any user to swap via the standard periphery path — the allowlist is completely defeated. Any unprivileged user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and swap on the curated pool. LP funds are exposed to counterparties the pool admin explicitly intended to exclude, constituting a direct loss of LP principal and a broken core pool access-control invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps. Pool admins who want their allowlisted users to be able to use the router must allowlist the router address. This is the expected operational path. The bypass is therefore reachable by any user on any pool that has enabled router-mediated swaps, which is the common production configuration.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **originating economic actor**, not the intermediary. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the originating user (`msg.sender` of the router call) as a verified field in `extensionData` or as a dedicated parameter, and have the extension read it from there after verifying the pool is the caller.

2. **In `SwapAllowlistExtension`**: check the `recipient` or a user-supplied identity field from `extensionData` rather than `sender` when the caller is a known router, or require the pool to forward the true originator through a dedicated mechanism.

The cleanest fix is for the pool's `swap` interface to carry a separate `originator` field that the router populates with `msg.sender` before calling the pool, and for the extension to gate on that field.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address.

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        ...
    })
  - Router calls pool.swap(attacker, ...) with msg.sender = router.
  - Pool calls _beforeSwap(router, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  - Attacker receives output tokens despite never being allowlisted.

Expected: revert NotAllowedToSwap()
Actual:   swap executes successfully
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
