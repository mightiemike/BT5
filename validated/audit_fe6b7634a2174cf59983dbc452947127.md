### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the allowlist check is performed against the router address, not the end user. This creates an irreconcilable dilemma: either the router is allowlisted (any user bypasses the per-user gate) or it is not (router-mediated swaps are permanently broken on curated pools).

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards this value to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap()`, so `sender` = router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The allowlist state is keyed by `(pool, swapper)`: [3](#0-2) 

For a curated pool that intends to gate individual users, the pool admin must choose between two broken states:

1. **Allowlist the router** → `allowedSwapper[pool][router] = true` → every user who calls the router bypasses the per-user gate entirely.
2. **Do not allowlist the router** → every router-mediated swap reverts with `NotAllowedToSwap`, breaking the supported periphery path for all users.

The analog to the HyperEvmVault bug is exact: the wrong actor (`sender` = router intermediary) is checked instead of the economically relevant actor (the end user who initiated the trade).

---

### Impact Explanation

**Severity: High**

On any pool configured with `SwapAllowlistExtension` (e.g., a KYC-gated or institutional pool), an unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The pool admin's intent — to restrict swaps to a specific set of addresses — is completely defeated. The disallowed user executes swaps at oracle-derived prices, extracting value from the pool's LPs who deposited under the assumption that only vetted counterparties could trade.

This matches the allowed impact gate: **broken core pool functionality causing loss of funds** (LP assets exposed to unvetted counterparties) and **admin-boundary break** (allowlist policy bypassed by an unprivileged path through a supported public contract).

---

### Likelihood Explanation

**Likelihood: High**

- `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users.
- Any user who is blocked by a direct `pool.swap()` call can trivially re-route through the router.
- No special privileges, flash loans, or unusual token behavior are required.
- The bypass is deterministic and requires only a single transaction.

---

### Recommendation

The extension must check the actual end user, not the intermediary. Two approaches:

1. **Pass the originating user through the router**: Have `MetricOmmSimpleRouter` encode the real `msg.sender` in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address. This requires a trusted router identity check (e.g., `onlyRouter` modifier or factory-registered router list).

2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable in restricted-router contexts): Replace `sender` with `tx.origin` in the allowlist lookup. This is only safe if the pool is exclusively accessed through EOA-initiated transactions.

The cleanest fix is approach (1): the pool or extension should be aware of the router as a trusted forwarder and unwrap the real initiator from `extensionData` before performing the allowlist check.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowlisted for any router swap to work
  - bob is NOT allowlisted

Attack:
  1. bob calls pool.swap() directly
     → pool calls extension.beforeSwap(bob, ...)
     → allowedSwapper[pool][bob] == false → REVERT ✓ (guard works)

  2. bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
     → router calls pool.swap()
     → pool calls extension.beforeSwap(router, ...)
     → allowedSwapper[pool][router] == true → PASSES ✗ (guard bypassed)
     → bob's swap executes at oracle price on the curated pool
``` [2](#0-1) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
