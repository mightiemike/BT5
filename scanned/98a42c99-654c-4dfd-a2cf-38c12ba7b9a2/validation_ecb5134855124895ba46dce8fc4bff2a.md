### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter — the direct `msg.sender` of `pool.swap()` — against the per-pool allowlist. When users swap through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual user. If the router is allowlisted for a pool (a natural configuration so that allowlisted users can use the router), any unprivileged user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`, the guard checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension), and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(
    address sender,   // ← this is msg.sender of pool.swap()
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

And in `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the sender:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of swap() — the router, not the user
    recipient,
    ...
);
``` [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, so `msg.sender` of `pool.swap()` = router address. The allowlist check resolves to:

```
allowedSwapper[pool][router]   ← checks the router, not the actual user
```

instead of the intended:

```
allowedSwapper[pool][user]     ← what the pool admin configured
```

This is the direct analog to the external bug: just as `getQuantAMMUpliftFeeTake()` is called in the swap-fee context where `getQuantAMMSwapFeeTake()` should be used, here `sender` (the router) is checked in the user-gating context where the actual user's identity should be checked. Both cases use the wrong value for the wrong context, with the wrong value being a plausible-looking alternative that silently passes.

The `SwapAllowlistExtension` exposes a setter that pool admins use to allowlist specific swappers:

```solidity
function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
``` [4](#0-3) 

A pool admin who wants to restrict swaps to a whitelist of users will naturally also allowlist the router so that those users can access the pool through the standard periphery. Allowlisting the router, however, opens the gate to every user of the router, because the extension checks `allowedSwapper[pool][router]` — which is `true` — for every router-mediated swap regardless of who the actual user is.

---

### Impact Explanation

Any unprivileged user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter` when the router is allowlisted for that pool. This breaks the core access-control invariant of the extension: pools configured to restrict swaps to specific participants (e.g., KYC'd LPs, protocol-owned addresses, or specific market makers) become fully open to arbitrary swappers. Depending on the pool's economic design, this can cause:

- Unauthorized arbitrage draining LP value from a pool intended for controlled participants.
- Violation of regulatory or protocol-level access restrictions with direct fund impact on LPs.
- Broken core pool functionality: the allowlist guard is rendered ineffective for all router-mediated swaps.

This matches the allowed impact gate: **admin-boundary break** (an unprivileged path bypasses a configured access control) and **broken core pool functionality causing loss of LP assets**.

---

### Likelihood Explanation

Medium. The trigger condition — the router being allowlisted for a pool — is a natural and expected configuration. Any pool admin who wants allowlisted users to be able to use the standard router will allowlist it. The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. The bypass requires no special privileges, no malicious setup, and no non-standard tokens: any user can call the router.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two options:

1. **Pass the original user via `extensionData`**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check it when present. This requires a convention between the router and the extension.

2. **Document and enforce router non-allowlisting**: Explicitly document that allowlisting the router grants access to all router users, and provide a separate mechanism (e.g., a router-aware extension) for pools that need both router access and user-level gating.

The simplest safe fix is option 1, with a fallback to checking `sender` when no user address is encoded in `extensionData`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to allow `userA` to use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The router calls `pool.swap(recipient=userB, ...)` with `msg.sender = router`.
6. `_beforeSwap(sender=router, ...)` is dispatched to the extension.
7. Extension checks `allowedSwapper[pool][router]` → `true`.
8. `userB` successfully swaps on the restricted pool — the allowlist is fully bypassed. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
