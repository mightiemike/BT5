### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual trader when swaps are routed through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract, not the actual user. If a pool admin allowlists the router address (a natural action to enable router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    ...
    _beforeSwap(
        msg.sender,   // ← whoever called pool.swap
        recipient,
        ...
    );
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

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

When a user calls `MetricOmmSimpleRouter.exactInput` (or any `exact*` entry point), the router calls `pool.swap(...)` on the user's behalf. At that point:

| Call path | `sender` seen by extension | Allowlist lookup |
|---|---|---|
| User → Pool directly | `user` | `allowedSwapper[pool][user]` ✓ |
| User → Router → Pool | `router` | `allowedSwapper[pool][router]` ✗ |

A pool admin who wants to enable router-mediated swaps will allowlist the router address. Once `allowedSwapper[pool][router] = true`, the guard passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. The per-user curation the admin intended is silently voided.

---

### Impact Explanation

Any user — including those explicitly excluded from the per-pool swap allowlist — can bypass the guard by routing through `MetricOmmSimpleRouter`. On a curated pool (e.g., restricted to KYC'd counterparties or specific market makers), this allows unauthorized traders to execute swaps against the LP, draining value from the pool at oracle-derived prices. The LP's principal is directly at risk because the pool will settle trades at the configured bid/ask regardless of who the counterparty is.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a natural and expected administrative action: the router is the protocol's own supported periphery contract, and an admin who wants users to be able to use it must allowlist it. The admin has no way to distinguish "allow the router for my allowlisted users only" from "allow the router for everyone" because the extension only sees the router's address, not the originating user. Once the router is allowlisted, any unprivileged user can exploit the bypass with a single router call.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it to extensions as a distinct `originator` field. Alternatively, `SwapAllowlistExtension.beforeSwap` should check `tx.origin` when `sender` is a known periphery contract, or the router should be redesigned to pass the user's address as an explicit parameter that the pool forwards to extensions instead of `msg.sender`.

A simpler short-term fix: document that allowlisting the router address is equivalent to `allowAllSwappers = true` and that per-user gating is only enforceable on direct pool calls.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Admin calls `setAllowedToSwap(pool, attacker, false)` (or simply never allowlists `attacker`).
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(...)` — `msg.sender` of `pool.swap` is the router.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps on a pool they were never individually authorized to access. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
