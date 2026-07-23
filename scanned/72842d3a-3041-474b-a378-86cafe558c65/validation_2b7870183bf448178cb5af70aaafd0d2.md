### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any unprivileged caller to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, that `sender` is the **router contract address**, not the actual end user. If the pool admin allowlists the router (the only way to let allowlisted users reach the pool through the router), every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap`: [4](#0-3) 

So `sender` arriving at the extension is the **router's address**, not the end user's address. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user bypasses the gate by routing through the router |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users.

---

### Impact Explanation

Any non-allowlisted user can trade in a pool that the admin intended to restrict (e.g., KYC-gated, institution-only, or whitelist-only pools). The allowlist guard is rendered completely ineffective for router-mediated swaps. This is a direct loss of the pool admin's access-control invariant and allows unauthorized parties to execute swaps, drain liquidity, or manipulate pool state in a pool they were explicitly excluded from.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the natural, expected operational step. Once the router is allowlisted, the bypass is trivially reachable by any EOA or contract with no special privileges, no flash loans, and no complex setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The router must forward the original end-user address to the pool so the extension can gate on it. Two approaches:

1. **Pass the real payer/initiator in `callbackData` and expose it via a standardized field in `extensionData`** so the extension can read the true originator.
2. **Have the extension read the payer from the router's transient storage** (the router already stores the payer in transient slot `T_SLOT_PAY_PAYER`), or require the router to include the originating user in `extensionData` so `SwapAllowlistExtension` can decode and check it instead of `sender`.

A minimal patch to `SwapAllowlistExtension` alone is insufficient because the root cause is that `sender` is the router. The fix must ensure the actual end user's address reaches the extension.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, router, true)   // must do this for any router user to work
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  4. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
     → router calls pool.swap(recipient, ...) with msg.sender = router
     → pool calls extension.beforeSwap(sender = router, ...)
     → extension checks allowedSwapper[pool][router] == true  ✓
     → swap executes for attacker with no allowlist enforcement

Result:
  attacker swaps in a pool they were explicitly excluded from.
  allowedSwapper[pool][attacker] was never set to true.
```

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
