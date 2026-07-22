### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router address**, not the originating user. The extension therefore checks whether the router is allowlisted, not whether the actual trader is allowlisted. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput` (or any router entry point), the router calls `pool.swap(...)` directly. Inside the pool, `msg.sender` is the **router contract**, so `sender` delivered to the extension is the router address — not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, which is `false` unless the pool admin explicitly allowlisted the router contract itself.

Two exploitable outcomes follow:

1. **Bypass (router is allowlisted or `allowAllSwappers` is true):** Any non-allowlisted user routes through the router and the extension passes, because the router's address satisfies the check.
2. **Lockout (router is not allowlisted):** Legitimately allowlisted users cannot use the router at all; only direct `pool.swap` calls work, breaking the intended UX and any integration that depends on the router.

The `DepositAllowlistExtension` has a parallel structure: `beforeAddLiquidity` checks the `owner` parameter, which the `MetricOmmPoolLiquidityAdder` controls and can set to any address, creating a symmetric wrong-actor binding on the deposit path. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or trusted counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-derived prices on a pool that was designed to be closed to them, draining LP value or extracting arbitrage that the curation policy was meant to prevent. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed alongside the protocol. Any user aware of the router can exploit this without any privileged access, special tokens, or setup. The trigger is a single standard router call. Likelihood is **high**.

---

### Recommendation

The pool must forward the **originating user** as `sender`, not `msg.sender`. Two standard approaches:

1. **Trusted forwarder pattern:** The router passes the original `msg.sender` in `callbackData` or a dedicated parameter, and the pool verifies the router's identity before trusting the forwarded address.
2. **Check `tx.origin` as a fallback (not recommended for general use):** Only acceptable in narrow, non-composable contexts.

The cleanest fix is to add an explicit `swapper` parameter to `pool.swap` that the pool records from the caller, and have the router pass `msg.sender` (the user) as that argument. The extension then checks the declared swapper rather than the pool's `msg.sender`.

Alternatively, the `SwapAllowlistExtension` documentation must explicitly state that pools using it must also allowlist every router or periphery contract that users are expected to call, and the router must be treated as a trusted intermediary with its own separate access tier.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin does NOT allowlist the router

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
    targeting the curated pool
  - Router calls pool.swap(recipient=bob, ...)
  - Inside pool: msg.sender = router
  - _beforeSwap(router, ...) is called
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → false
  - Swap reverts with NotAllowedToSwap

Alternate attack (router is allowlisted):
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required to let alice use the router)
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
  - Router calls pool.swap(recipient=bob, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap succeeds — Bob bypasses the allowlist entirely
```

In both cases the guard is misapplied: it either locks out legitimate users or passes for illegitimate ones, depending solely on whether the router address appears in the allowlist. [5](#0-4) [3](#0-2)

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
