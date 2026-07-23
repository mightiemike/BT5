### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Complete Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the user's address. If the pool admin allowlists the router (which is necessary for any allowlisted user to use the router), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` of the `swap()` call as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← this is the router when the user goes through the router
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So the extension receives `sender = address(router)`, not the actual user's address. The extension has no mechanism to recover the real user identity from `extensionData` — it ignores that field entirely.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd users, protocol-controlled addresses, or institutional counterparties). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` with the restricted pool as the target. The allowlist guard silently fails open for all router-mediated swaps once the router is allowlisted, which is the only way to make the router usable for legitimate allowlisted users. This breaks the core access-control invariant of curated pools and allows unauthorized fund flows through the pool.

---

### Likelihood Explanation

**Medium.** The router is the primary user-facing swap interface for the protocol. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. This is a natural and expected administrative action. Once done, the bypass is immediately available to any address with no further preconditions, no special tokens, and no privileged access.

---

### Recommendation

The extension must be able to identify the real initiating user, not the intermediary contract. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks this value. This requires a trusted router (the extension must verify the caller is a known router before trusting the encoded identity).

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers. When `sender` is a trusted router, it reads the actual user identity from `extensionData`; otherwise it checks `sender` directly.

Either approach requires the extension to be aware of the router layer, which is the fundamental fix: the allowlist must gate the economically relevant actor (the user), not the transport layer (the router).

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists `userA` and the router: `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)`.
3. `userB` (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userB` successfully swaps in a pool they were never authorized to access.

The allowlist guard is completely transparent to any user who routes through the router once the router is allowlisted.

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
