### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of the pool's `swap` call — against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the originating user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for any user), every unprivileged address can bypass individual swapper restrictions by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded from `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) is called by a user, the router calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

At this point `msg.sender` of the pool's `swap` is the **router**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for any pool admin who wants to:
- Restrict swaps to a curated set of users (e.g., KYC-verified addresses), **and**
- Allow those users to interact via the supported periphery router.

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps revert — even for allowlisted users |
| Yes | Every address on-chain can bypass individual restrictions via the router |

There is no configuration that achieves both goals simultaneously.

---

### Impact Explanation

Any unprivileged address can trade in a pool that is supposed to be restricted to a curated allowlist, simply by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant). The router is a public, permissionless contract. Once the pool admin allowlists the router — a natural and expected operational step — the allowlist provides zero protection against unauthorized swappers. Unauthorized swaps can drain LP-owned liquidity at oracle-derived prices, constituting a direct loss of LP principal and a broken core pool invariant (curated access control).

---

### Likelihood Explanation

Medium-High. The pool admin allowlisting the router is the expected production configuration for any pool that intends to support the standard periphery UX. The bypass requires no special privileges, no flash loans, and no non-standard tokens — only a call to the public router. Any user aware of the router address can exploit this immediately after the router is allowlisted.

---

### Recommendation

The `beforeSwap` hook must identify the **economically responsible actor**, not the proximate caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the user; however this is not always equivalent.
3. **Dedicated router forwarding**: The router exposes a verified `originalSender()` view (stored in transient storage alongside the callback context) that the extension can read directly, similar to how the callback context already stores the payer.

The `DepositAllowlistExtension` correctly gates `owner` (the LP position owner) rather than `sender` (the proximate caller), which is the right pattern for the deposit side. [4](#0-3) 

The swap allowlist should adopt an equivalent design — checking the actor to whom the economic action is attributed, not the contract that forwarded the call.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` registered in the `beforeSwap` order.
2. Pool admin allowlists Alice (a KYC-verified user): `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use the standard UI: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted, not KYC-verified) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient=bob, ...)` — `msg.sender` of the pool call is the router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes at oracle-derived prices, draining pool liquidity that was intended only for curated counterparties.

The allowlist is fully bypassed without any privileged access, flash loan, or non-standard token behavior.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
