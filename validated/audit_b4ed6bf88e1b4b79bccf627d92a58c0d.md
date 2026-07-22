### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap` function passes `msg.sender` (the router contract) as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. Any pool admin who allowlists the router to permit allowlisted users to trade through it simultaneously opens the gate for every non-allowlisted user to bypass the restriction through the same router path.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`, which relays it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses `msg.sender` (the pool) as the mapping key and the received `sender` argument as the identity to gate: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`. At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router, not the originating user. The extension evaluates `allowedSwapper[pool][router]` — a single boolean that is either true for every user or false for every user. There is no per-user discrimination possible through the router path.

The pool admin faces an inescapable dilemma:

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Yes | ✓ can swap | ✓ bypass — **vulnerability** |
| No | ✗ broken | ✗ blocked |

The `onlyPool` modifier present on the base class `beforeSwap` is also silently dropped in the override, meaning the extension can be called by any address — though the primary impact is the router bypass described above: [4](#0-3) [3](#0-2) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the router is allowlisted — which is operationally required for any allowlisted user to trade through the supported periphery — the restriction is completely nullified for all users. Any non-allowlisted address can execute swaps against the pool's full liquidity, receiving tokens out and paying tokens in, with no restriction. This constitutes a direct bypass of a core pool access-control mechanism with fund-impacting consequences (unauthorized parties drain or trade against LP-owned assets).

---

### Likelihood Explanation

The bypass requires only that the pool admin has allowlisted the router (the expected operational configuration). The attacker needs no special privilege, no flash loan, and no multi-transaction setup — a single call to the router suffices. The router is a canonical, publicly deployed periphery contract, so the attack surface is permanently reachable.

---

### Recommendation

The pool's `swap` function must expose the originating user's address to extensions independently of `msg.sender`. Two viable approaches:

1. **Add an explicit `originator` parameter to `pool.swap`** that the router populates with `msg.sender` (the actual user). The pool passes this through to `_beforeSwap` alongside the existing `sender`. The extension gates on `originator` when non-zero.

2. **Move allowlist enforcement into the router** so the router rejects non-allowlisted callers before calling the pool, and the pool-level extension is removed or made router-aware. This requires the router to be the canonical and only entry point for allowlisted pools.

Additionally, restore the `onlyPool` modifier in `SwapAllowlistExtension.beforeSwap` (and `DepositAllowlistExtension.beforeAddLiquidity`) so that only registered pools can invoke the hook:

```solidity
// current — modifier silently dropped
function beforeSwap(...) external view override returns (bytes4) { ... }

// fixed
function beforeSwap(...) external view override onlyPool returns (bytes4) { ... }
```

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls SwapAllowlistExtension.setAllowedToSwap(pool, alice, true)
   — Alice is the only allowlisted swapper.
3. Pool admin calls SwapAllowlistExtension.setAllowedToSwap(pool, router, true)
   — Router is allowlisted so Alice can trade through the supported periphery.

Attack (Bob is not allowlisted)
────────────────────────────────
4. Bob calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
5. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData).
   → Inside pool.swap: msg.sender == router
   → _beforeSwap(router, recipient, ...) is called.
6. SwapAllowlistExtension.beforeSwap receives sender == router.
   → Checks: allowedSwapper[pool][router] == true  ✓
   → Does NOT revert.
7. Swap executes. Bob receives output tokens. Allowlist is bypassed.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [5](#0-4) [3](#0-2) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
