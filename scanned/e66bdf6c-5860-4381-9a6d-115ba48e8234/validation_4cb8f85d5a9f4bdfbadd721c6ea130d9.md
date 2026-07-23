### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural configuration for a pool that wants to support periphery-mediated swaps), every user — including those the allowlist was meant to exclude — can bypass the restriction by routing through the router.

---

### Finding Description

**Pool `swap()` always passes `msg.sender` as `sender` to the extension:**

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

**`SwapAllowlistExtension.beforeSwap` checks that argument against the per-pool allowlist:**

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the end user's address. The extension has no way to recover the original user from this argument.

**Bypass path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and intends to gate specific KYC'd or whitelisted addresses.
2. Admin calls `setAllowedToSwap(pool, routerAddress, true)` so that router-mediated swaps work for allowlisted users (or simply to enable the standard periphery flow).
3. Any unpermissioned user calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. The router calls `pool.swap(...)` — `msg.sender` to the pool is the router.
5. The extension evaluates `allowedSwapper[pool][routerAddress]` → `true` → swap proceeds.
6. The unpermissioned user receives tokens from the curated pool, bypassing the allowlist entirely.

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards whatever `sender` the pool provides; there is no secondary check that recovers the original EOA:

```solidity
// ExtensionCalling.sol – _beforeSwap
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
```

---

### Impact Explanation

A curated pool relying on `SwapAllowlistExtension` to restrict trading to approved counterparties is fully open to any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps, draining pool liquidity at oracle-derived prices. Because the pool is oracle-anchored and has no internal price discovery, every unauthorized swap extracts real value from LP positions at the current fair-market price. This constitutes a direct loss of LP principal and breaks the core invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it.

---

### Likelihood Explanation

Likelihood is **High**. The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router address — there is no other mechanism. Once the router is allowlisted, the bypass is immediately available to any public caller with zero additional preconditions. No privileged access, no special token, and no admin cooperation is required beyond the natural configuration step.

---

### Recommendation

The extension must gate the **end user**, not the intermediary contract. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply honest data, which is acceptable given it is a protocol-controlled contract.

2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a router, extract the real user from `extensionData`; when `sender` is an EOA, check it directly.

Either way, the invariant must be: **the address checked against the allowlist is the address that economically benefits from the swap (the recipient or the originating user), not the settlement intermediary.**

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  admin calls setAllowedToSwap(pool, address(router), true)
  alice (allowlisted EOA) and bob (non-allowlisted EOA) both hold token1

Attack:
  bob calls MetricOmmSimpleRouter.exactInput(
      path = [token1 → pool → token0],
      recipient = bob,
      amountIn = X
  )

  Router calls pool.swap(recipient=bob, zeroForOne=false, ...)
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true → no revert
  Pool executes swap, sends token0 to bob, pulls token1 from router (which pulls from bob)

Result:
  bob successfully swaps on a pool he is not allowlisted for.
  The allowlist is completely bypassed.
  LP providers suffer a loss equal to the swap output at oracle price.
```