### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the end-user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to gate pool swaps by swapper address. However, the `sender` value it receives is always `msg.sender` of `MetricOmmPool.swap()`. When a router intermediary calls the pool, `sender` equals the router's address, not the end-user's address. A pool admin who allowlists the router to support router-based swaps inadvertently opens the gate to every user of that router, completely defeating the individual-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist: [2](#0-1) 

When a user swaps through `MetricOmmSimpleRouter`, the call chain is:

```
user → MetricOmmSimpleRouter.swap() → MetricOmmPool.swap()
                                           msg.sender = router
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. For any router-based swap to succeed at all, the pool admin must allowlist the router address. Once the router is allowlisted, the `allowedSwapper[pool][router]` check passes for **every** user who routes through it, regardless of whether that individual user was ever intended to be permitted.

The `sender` parameter in `beforeSwap`'s signature is the only identity the extension can act on: [3](#0-2) 

There is no mechanism for the extension to recover the original end-user address; the pool hard-codes `msg.sender` and the extension interface exposes only what the pool passes.

The pool's `addLiquidity` exhibits the same structural pattern — `sender` is `msg.sender` of the pool call — but `DepositAllowlistExtension` intentionally checks `owner` (the position beneficiary), which is a documented operator pattern. The swap path has no equivalent "owner vs. sender" distinction; the extension's only identity input is `sender`, making the router substitution a silent bypass rather than an intentional design. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool that limits toxic flow to known market makers) loses that restriction entirely for any user who routes through an allowlisted router. Unauthorized users can execute swaps against the oracle-anchored bin liquidity, extracting LP assets at oracle-fair prices and causing direct loss of LP principal. The pool admin cannot simultaneously support router-based swaps for permitted users and block unpermitted users from using the same router.

---

### Likelihood Explanation

This occurs in every pool that:
1. Deploys `SwapAllowlistExtension` in its `beforeSwap` order, and
2. Allowlists `MetricOmmSimpleRouter` (or any other router) so that permitted users can swap without calling the pool directly.

Both conditions are the natural, expected configuration for a production pool that wants access control with router UX. The bypass is therefore triggered by normal, valid pool administration.

---

### Recommendation

The extension interface should be extended to carry the original end-user identity separately from the immediate caller. One approach: add an `originSender` field to `extensionData` that the router populates and the extension verifies (with the pool enforcing that `msg.sender` is a trusted router before trusting `originSender`). Alternatively, `SwapAllowlistExtension` can maintain a separate `trustedRouter` mapping and, when `sender` is a trusted router, extract and check the real user address from `extensionData`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1` in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps against LP liquidity despite never being individually allowlisted. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
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
