Let me check the `ExtensionCalling` contract to confirm how `_beforeSwap` passes arguments to the extension.

The complete call chain is now fully traced. Here is the verdict.

---

### Title
Router-Mediated Swaps Check the Router's Address Instead of the End User in `SwapAllowlistExtension::beforeSwap` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` received by the pool's `swap` function. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual swapper is allowlisted. This breaks the stated invariant of the extension ("Gates `swap` by swapper address, per pool") for all router-mediated flows.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:**

`MetricOmmPool::swap` calls `_beforeSwap(msg.sender, ...)`. [1](#0-0) 

**Step 2 — `ExtensionCalling::_beforeSwap` forwards that value unchanged as the first argument:**

```
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 3 — The extension checks `allowedSwapper[pool][sender]`:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Step 4 — The router calls `pool.swap(...)` as `msg.sender`:**

In `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`, the router is the direct caller of `pool.swap`. The original end user's address is stored only in transient storage as the `payer` for the callback — it is never forwarded to the pool as `sender`. [4](#0-3) 

**Result:** `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][user]`.

### Impact Explanation

Two concrete failure modes arise:

1. **Allowlist bypass (security):** A pool admin who wants their allowlisted users to also be able to use the router will naturally add the router to the allowlist. Once the router is allowlisted, *any* address — including non-allowlisted users — can bypass the per-user gate by routing through `MetricOmmSimpleRouter`. The extension's entire purpose (restricting swaps to approved counterparties) is silently nullified.

2. **Allowlisted users locked out of the router (usability/functionality):** If the pool admin does not allowlist the router, every allowlisted user who attempts a router-mediated swap is rejected, because the extension sees the router's address and finds it absent from the allowlist. This makes the router unusable for any pool that deploys `SwapAllowlistExtension` without explicitly allowlisting the router contract.

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) allowlists the router to enable router access for its users is immediately vulnerable. This is a natural and expected configuration: pool admins who restrict swaps to known counterparties will also want those counterparties to be able to use the standard router. The design flaw is not visible from the admin interface — `setAllowedToSwap(pool, router, true)` looks identical to allowlisting any other address.

### Recommendation

The extension must be able to identify the true end user, not the immediate caller. Two approaches:

1. **Pass the original user via `extensionData`:** The router encodes the original `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and verifies it. This requires a trust assumption that the router is the caller (verifiable via `msg.sender` inside the extension, which is the pool, and the pool's `msg.sender` being the router).

2. **Check the router's stored payer:** Expose a view on the router's transient payer and have the extension query it. This is architecturally complex and introduces cross-contract coupling.

3. **Document the limitation clearly:** If the design intent is that the router must be allowlisted as a unit (not per-user), document this explicitly and provide a separate `SwapAllowlistWithRouterExtension` that reads the payer from the router's transient context.

### Proof of Concept

```
Pool configured with SwapAllowlistExtension.
allowedSwapper[pool][alice] = true   // Alice is the only approved swapper
allowedSwapper[pool][router] = true  // Admin adds router so Alice can use it

// Attack:
// Bob (not allowlisted) calls:
router.exactInputSingle({pool: pool, recipient: bob, ...})
// Pool.swap is called with msg.sender = router
// beforeSwap receives sender = router
// allowedSwapper[pool][router] == true  → check passes
// Bob's swap executes despite not being on the allowlist
```

Direct swap by Bob:
```
pool.swap(bob, ...)
// beforeSwap receives sender = bob
// allowedSwapper[pool][bob] == false → reverts ✓
```

The allowlist is enforced for direct calls but silently bypassed for all router-mediated calls once the router is allowlisted.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
