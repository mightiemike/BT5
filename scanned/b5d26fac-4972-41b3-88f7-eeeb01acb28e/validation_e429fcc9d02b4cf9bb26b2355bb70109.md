### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the user. If the pool admin allowlists the router to permit router-mediated swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` seen by the pool is the **router**, so `sender` delivered to the extension is the router's address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Individually-allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the gate via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) is rendered ineffective the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade in the restricted pool, breaking the intended access boundary. LPs in such a pool are exposed to adverse-selection or volume from counterparties the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented in the periphery. A pool admin who wants allowlisted users to be able to use the router (the normal UX path) will naturally add the router to the allowlist. This is the expected operational action, and it silently opens the gate to all users. No special attacker capability is required — any EOA can call the router.

---

### Recommendation

The extension should gate the **economically relevant actor**, not the intermediary. Two options:

1. **Pass the original user through the router.** Have the router forward the originating `msg.sender` as an additional field in `extensionData`, and update `SwapAllowlistExtension` to decode and check that address when present.

2. **Check `recipient` instead of `sender` for router flows**, or require pools using this extension to be called directly (document that router-mediated swaps are incompatible with the allowlist).

The simplest safe fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it, falling back to `sender` when no override is present.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...)
   → pool calls _beforeSwap(msg.sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for Bob despite Bob not being allowlisted
5. Direct call: pool.swap(...) with msg.sender=bob → allowedSwapper[pool][bob]==false → reverts
   Bob bypasses the gate exclusively through the router.
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
