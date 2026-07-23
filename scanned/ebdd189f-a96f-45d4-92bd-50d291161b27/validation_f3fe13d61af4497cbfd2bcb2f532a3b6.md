Let me verify the exact call chain in the pool's `swap` function and how `sender` is bound.

The call chain is fully traced. Here is the determination:

---

### Title
Router-mediated swaps pass the router address as `sender` to `SwapAllowlistExtension`, breaking per-user allowlist enforcement — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract address**, not the end user. This makes it impossible to enforce a per-user allowlist for router-mediated swaps: either the router is not allowlisted (blocking all allowlisted users from using the router), or the router is allowlisted (allowing every user to bypass the per-user allowlist).

### Finding Description

**Step 1 — Pool binds `msg.sender` as `sender`:**

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — Router calls `pool.swap()` as itself:**

Every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly. The router never forwards the original `msg.sender` (the end user) to the pool:

```solidity
// exactInputSingle — msg.sender to pool = router address
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
``` [2](#0-1) 

**Step 3 — Extension checks the router address, not the end user:**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` = pool, `sender` = **router address** (not the end user). [3](#0-2) 

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Does NOT allowlist the router | Allowlisted users cannot use the router at all — supported periphery path is broken |
| Allowlists the router | Every user (including non-allowlisted ones) can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted addresses) cannot enforce that restriction when users route through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to let legitimate users use the router), any unprivileged user can call `router.exactInputSingle(...)` and trade on the curated pool, completely bypassing the allowlist. This is a direct curation failure: disallowed users can trade on pools that were designed to exclude them.

### Likelihood Explanation

The router is the primary supported public swap entrypoint. Any user who discovers that the router address is allowlisted on a curated pool can immediately exploit this. No special privileges, flash loans, or multi-transaction sequences are required — a single `exactInputSingle` call suffices.

### Recommendation

The `sender` passed to extension hooks should represent the **originating user**, not the immediate caller of `pool.swap()`. Options:

1. **Pass the original user through the router:** Add a `payer`/`originator` field to the pool's `swap` interface (or a separate hook argument) that the router populates with `msg.sender` before calling the pool. The extension then checks this field.
2. **Check `recipient` instead of `sender`:** If the pool's design intent is that the recipient is the economically relevant party, gate on `recipient`. However, this changes semantics for other use cases.
3. **Document that the router must never be allowlisted and provide a router-aware extension:** Deploy a `SwapAllowlistExtension` variant that decodes the true originator from `extensionData` (signed or passed by the router), and require the router to forward `msg.sender` in `extensionData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only allowed user)
  - allowedSwapper[pool][router] = true  (router must be allowlisted for alice to use it)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - beforeSwap receives sender = router
  - Check: allowedSwapper[pool][router] == true  → PASSES
  - Bob's swap executes on the allowlisted pool

Result:
  - Bob bypassed the allowlist entirely
  - Direct call: pool.swap() with msg.sender=bob → allowedSwapper[pool][bob]=false → REVERTS (correctly)
  - Router call: router.exactInputSingle() with msg.sender=bob → allowedSwapper[pool][router]=true → SUCCEEDS (bypass)
```

The asymmetry between the direct path and the router path is the root of the bypass. [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
