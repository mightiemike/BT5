### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass a curated pool's swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the end user's address. If the pool admin allowlists the router (to permit router-based swaps for legitimate users), every unpermissioned user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-231
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender = msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then checks that value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

At this point `msg.sender` to the pool is the **router contract**, so `sender` delivered to `beforeSwap` is `address(router)`, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`.

**Two concrete failure modes arise:**

1. **Router not allowlisted** — Legitimate allowlisted users cannot use the router at all; they must call the pool directly. Core swap functionality is broken for curated pools.

2. **Router allowlisted** (the only way to let allowlisted users use the router) — Every unpermissioned user can bypass the allowlist by routing through the same public `MetricOmmSimpleRouter`, because the check resolves to `allowedSwapper[pool][router] = true` for all of them.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, since every hop calls `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional desks, or whitelisted protocols) loses that restriction entirely once the router is allowlisted. Any anonymous user can trade against the pool's liquidity at oracle-derived prices, extracting value from LPs who deposited under the assumption that only vetted counterparties could interact. This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a standard, publicly deployed periphery contract. Any user who discovers that a pool uses a swap allowlist can trivially route through the router. No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices. The pool admin has no way to prevent this without removing the router from the allowlist, which simultaneously breaks the router for legitimate users.

---

### Recommendation

The allowlist must gate the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` (the economic beneficiary) instead of `sender`, or require the pool to pass the original initiator through `extensionData`.

2. **In `MetricOmmSimpleRouter`**: forward the original `msg.sender` inside `extensionData` so extensions can recover the true initiator. The extension can then decode and verify it.

The cleanest invariant-preserving fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to let Alice use the router
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // Alice is a legitimate user
  - Bob is NOT allowlisted.

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=Bob, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes. Bob receives tokens from the curated pool.

Expected: Bob's call should revert with NotAllowedToSwap.
Actual:   Bob's call succeeds because the router is allowlisted.
```

The call chain is fully reachable through the public `exactInputSingle`, `exactInput`, and `exactOutput` entry points on `MetricOmmSimpleRouter`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
