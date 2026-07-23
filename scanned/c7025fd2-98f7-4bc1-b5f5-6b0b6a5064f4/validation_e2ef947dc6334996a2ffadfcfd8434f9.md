### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged actor to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on the network can bypass the per-address allowlist by routing through the public router contract.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every registered extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter` (a public periphery contract), the router calls `pool.swap()` on the user's behalf. At that point `msg.sender` to the pool is the **router address**, so `sender` in the extension is the router, not the end user. The extension therefore checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

This is structurally identical to the AuraVault bug: the wrong domain value (router address instead of end-user address, just as `shares` instead of `assets`) is passed into the critical guard, causing the guard to evaluate the wrong entity.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) must also allowlist the router to support normal UX. Once the router is allowlisted, **any address** can call `MetricOmmSimpleRouter.exactInput/exactOutput` and the extension will pass, because it sees the router — not the caller — as the swapper. Non-allowlisted users can drain LP value from pools that were designed to be access-controlled, constituting a direct loss of LP principal and a broken core pool invariant (the allowlist guard).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. No special role or privilege is required to call it. Any user who knows the pool address can route through it. The bypass is trivially reachable on every pool that has `SwapAllowlistExtension` configured and the router allowlisted.

---

### Recommendation

The extension must gate the **originating user**, not the direct pool caller. Two complementary fixes:

1. **Pass the end-user through the router**: Have `MetricOmmSimpleRouter` forward the original `msg.sender` inside `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of (or in addition to) `sender`.

2. **Check `sender` and `recipient` together**: If the router is the `sender`, decode the true initiator from `extensionData` and verify it against the allowlist.

Alternatively, document that pools using `SwapAllowlistExtension` must **not** allowlist the router, and must require users to call `pool.swap()` directly — but this breaks normal UX and is not enforced on-chain.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin allowlists only `trustedUser` via `setAllowedToSwap(pool, trustedUser, true)`.
3. Admin also allowlists the router via `setAllowedToSwap(pool, router, true)` (required for router-mediated swaps to work).
4. `attackerUser` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, recipient, ...)` is dispatched to the extension.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `attackerUser` successfully swaps against the restricted pool, bypassing the per-address allowlist entirely. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
}
```
