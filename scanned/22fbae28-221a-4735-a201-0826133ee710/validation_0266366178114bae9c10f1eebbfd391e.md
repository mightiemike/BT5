### Title
SwapAllowlistExtension gates on router address instead of end-user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the end-user. If the router is allowlisted (the natural configuration for any pool that accepts router-mediated swaps), every non-allowlisted user can bypass the swap allowlist by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap()`, making the router the `msg.sender` of that call. The pool therefore passes the **router's address** as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to allow router-mediated swaps must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every call that arrives through the router — regardless of who the actual end-user is. Any non-allowlisted user can bypass the gate by calling the router instead of the pool directly.

The `SwapAllowlistExtension` also exposes `setAllowedToSwap` and `setAllowAllSwappers`, both gated on `onlyPoolAdmin`, so the misconfiguration is not correctable at the extension level without removing the router from the allowlist entirely — which would break legitimate router-mediated swaps for allowlisted users. [4](#0-3) 

---

### Impact Explanation

The swap allowlist is a core access-control extension. Pools deploy it to restrict swapping to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or protocol-controlled routers only). A complete bypass of this gate means:

- Non-allowlisted users can execute swaps against restricted pools, draining LP liquidity at oracle-anchored prices the pool admin intended to expose only to trusted parties.
- Any fee-capture or spread-protection logic that depends on the allowlist being enforced is rendered ineffective.
- LP principal is at direct risk because the pool's liquidity is consumed by actors the pool admin explicitly excluded.

This matches the **broken core pool functionality causing loss of funds** and **admin-boundary break** impact categories.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it.
- A pool admin who deploys a `SwapAllowlistExtension` and also wants to support router-mediated swaps for allowlisted users **must** allowlist the router. This is the expected operational configuration.
- Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no malicious setup, and no non-standard tokens.
- The attacker only needs to call `MetricOmmSimpleRouter.exactInput` (or any `exact*` variant) targeting the restricted pool.

---

### Recommendation

The extension must gate on the **end-user identity**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` should forward the caller's address as an authenticated field in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that field instead of (or in addition to) `sender`.

2. **Check `sender` only when it equals the pool's direct caller.** Alternatively, the extension can require that `sender` is never a known router/intermediary, but this is fragile. The cleaner fix is option 1.

A parallel issue exists in `DepositAllowlistExtension`: the `beforeAddLiquidity` hook receives both `sender` (the `msg.sender` of `addLiquidity`) and `owner` (the position owner). If the extension checks `owner`, a non-allowlisted user can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedOwner, ...)`, paying tokens while the allowlisted owner receives LP shares — bypassing the deposit gate on the payer side. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker EOA.

Attack:
  1. attacker (non-allowlisted EOA) calls MetricOmmSimpleRouter.exactInput(...)
     targeting the restricted pool.
  2. Router calls pool.swap(recipient, ...).
  3. pool.swap passes msg.sender = router as `sender` to _beforeSwap.
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  5. Swap executes. Attacker receives output tokens.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; allowlist is fully bypassed.
``` [3](#0-2) [6](#0-5) [2](#0-1)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
