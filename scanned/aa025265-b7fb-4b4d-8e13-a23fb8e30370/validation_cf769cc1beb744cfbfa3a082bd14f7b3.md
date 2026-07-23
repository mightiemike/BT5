The call chain is fully traceable. Here is the complete analysis:

---

## Call Chain Trace

**Direct swap path:**
```
User → pool.swap(msg.sender=User) → _beforeSwap(sender=User) → extension.beforeSwap(sender=User)
Check: allowedSwapper[pool][User]  ✓ correct identity
```

**Router path:**
```
User → router.exactInputSingle() → pool.swap(msg.sender=Router) → _beforeSwap(sender=Router) → extension.beforeSwap(sender=Router)
Check: allowedSwapper[pool][Router]  ✗ wrong identity
```

The identity shift is confirmed by three code points:

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`: [4](#0-3) 

---

## Bypass Condition Analysis

| Router allowlisted? | Non-allowlisted user via router | Allowlisted user via router |
|---|---|---|
| No | Blocked (router not in list) | Also blocked (router not in list) |
| **Yes** | **Passes — bypass** | Passes |

The bypass is **conditional**: it requires `allowedSwapper[pool][router] = true`. Without that, the router itself is blocked and no bypass occurs.

**Is allowlisting the router a realistic admin action?** Yes. A pool admin who wants their allowlisted users to be able to use the standard periphery router would naturally add the router to the allowlist. Doing so silently opens the pool to all users, defeating the per-user curation entirely.

---

## Verdict

### Title
SwapAllowlistExtension checks router identity instead of original user when swaps are routed through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by the `sender` argument, which the pool sets to its own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. The extension checks `allowedSwapper[pool][sender]`. When the router is the caller, `sender = router`. If a pool admin allowlists the router (a natural step to support router-based swaps for their curated users), the check becomes `allowedSwapper[pool][router] == true`, which passes for **any** user who routes through the router — including users who are not individually allowlisted.

### Impact Explanation
A curated pool's per-user allowlist is completely bypassed for any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted address can trade on a pool that was intended to be restricted (e.g., KYC-gated, institutional-only). This is a curation failure: the pool admin's access control is silently nullified by the supported public periphery path.

### Likelihood Explanation
Likelihood is **medium**. The bypass requires the pool admin to have allowlisted the router. This is a natural and expected configuration for any pool that wants to support both direct and router-based swaps for its allowlisted users. The admin has no indication from the extension's interface or documentation that doing so opens the pool to all users.

### Recommendation
The `SwapAllowlistExtension` should not rely solely on the immediate `sender` (the direct caller of `pool.swap`). Options:
1. Pass the original user's address through `extensionData` and verify it in the hook (requires router cooperation and trust).
2. Document explicitly that allowlisting the router grants access to all users, and provide a separate `RouterSwapAllowlistExtension` that reads the payer from transient storage set by the router.
3. Have the router write the original `msg.sender` into a trusted transient slot that the extension can read, so the hook always sees the end-user identity.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `beforeSwap(sender=router, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Bob's swap executes on the allowlist-gated pool despite not being individually allowlisted.

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
