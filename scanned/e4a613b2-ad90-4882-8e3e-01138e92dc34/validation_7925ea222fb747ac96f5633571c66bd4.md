### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the actual user. The extension therefore checks whether the router is allowlisted, not the real swapper. Any user can bypass a restricted pool's allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value just described: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the **router**, not the end user: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Consequence — two broken states:**

| Pool admin intent | What actually happens |
|---|---|
| Allowlist the router so legitimate users can use it | `allowedSwapper[pool][router] = true` → **every user** passes the check; allowlist is fully bypassed |
| Do NOT allowlist the router | Allowlisted users cannot swap through the router at all; the guard is misapplied in the opposite direction |

Neither state matches the intended semantics of "gate swaps by individual swapper identity."

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) can be bypassed by any unprivileged user simply by calling `MetricOmmSimpleRouter.exactInputSingle`. The user receives pool output tokens and the pool receives input tokens from an actor the pool admin never intended to allow. This is a direct loss of the pool's access-control invariant and constitutes unauthorized extraction of LP-owned assets from a restricted pool.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token balance, or prior state is required. Any EOA or contract can call `exactInputSingle` with a target pool address. The bypass is reachable on every swap against every allowlist-gated pool that the router can reach.

---

### Recommendation

The extension must verify the **original user**, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the original payer through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `recipient` or require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory or extension initialization level.

The cleanest fix is for the pool to expose the original initiator (e.g., via a dedicated field in the hook arguments) rather than relying on `msg.sender`, which is always the immediate caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for alice to use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  - _beforeSwap(sender=router, ...) is dispatched
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Bob's swap succeeds despite not being on the allowlist

Result:
  - Bob extracts tokens from a pool that was configured to exclude him
  - The allowlist guard is silently bypassed with no revert
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
