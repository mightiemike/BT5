### Title
`SwapAllowlistExtension` checks router address instead of end-user address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always sets `sender = msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the **router's address** against the allowlist instead of the **end user's address**. Any pool admin who allowlists the router (a natural operational choice) inadvertently opens the allowlist to every user on earth.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap()` on the user's behalf. The pool's `swap()` signature has no explicit `sender` parameter — it only exposes `recipient`: [4](#0-3) 

So `msg.sender` inside `pool.swap()` is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

Contrast this with `DepositAllowlistExtension`, which correctly checks `owner` — an explicit parameter the caller supplies — rather than `msg.sender`: [5](#0-4) 

The deposit extension is safe because `owner` is a first-class parameter of `addLiquidity`. The swap extension has no equivalent; the pool's `swap()` function provides no way for the router to forward the original user's identity.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers). If the pool admin allowlists the router — a natural operational step so that users can access multi-hop routing — the allowlist is silently voided: every user who calls the router can trade on the pool regardless of whether their own address is on the list. The result is unauthorized swap execution on a pool whose entire value proposition is curation, with direct fund-impact consequences: non-allowlisted users drain liquidity from LP positions that were deposited under the assumption that only vetted counterparties could trade.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entrypoint documented alongside the pool. Pool admins who deploy a `SwapAllowlistExtension` pool and want users to access it through the router must allowlist the router — there is no other way to enable router-mediated swaps on an allowlisted pool. The bypass is therefore triggered by the standard operational setup, not by an exotic configuration. Any non-allowlisted user who discovers the router path can exploit it immediately without any privileged access.

---

### Recommendation

The `swap()` function on `MetricOmmPool` should accept an explicit `sender` parameter (analogous to `owner` in `addLiquidity`) so that the router can forward the original user's address. The pool would then pass this explicit `sender` to `_beforeSwap` instead of `msg.sender`. Alternatively, `SwapAllowlistExtension` could read the original user from a trusted forwarding field in `extensionData`, but this is fragile and requires router cooperation. The cleanest fix is a first-class `sender` parameter on `pool.swap()`, mirroring the `owner` pattern already used on `addLiquidity`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Attacker successfully swaps on a pool they were never authorized to access.

The check that should have blocked the attacker — `allowedSwapper[pool][attacker]` — is never evaluated. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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
