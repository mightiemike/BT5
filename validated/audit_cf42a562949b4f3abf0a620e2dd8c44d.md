### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the **router's address**, not the originating user's EOA. A pool admin who allowlists the router (the only way to enable router-mediated swaps for their curated pool) simultaneously opens the gate to every non-allowlisted user, because any EOA can call the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to every extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim as the `sender` argument in the ABI-encoded call to each configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender` (the pool's `msg.sender`) against the per-pool allowlist.**

```solidity
// msg.sender here == pool; sender == whoever called pool.swap()
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Step 3 — The router is the `msg.sender` of `pool.swap()`, not the originating EOA.**

`MetricOmmPool.swap()` has no `sender` parameter; the pool always uses `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool sees the router as `msg.sender`, so `sender` passed to every extension is the **router address**. [4](#0-3) 

**Step 4 — The pool admin faces an impossible choice.**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted EOAs cannot use the router; core swap path is broken for them |
| **Allowlist the router** | `allowedSwapper[pool][router] = true` → every EOA that calls the public router passes the check; per-user allowlist is nullified |

There is no configuration that simultaneously (a) allows router-mediated swaps and (b) enforces per-user allowlisting, because the extension has no visibility into the originating EOA. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, whitelisted market makers) is fully bypassed the moment the pool admin allowlists the router. Any non-allowlisted EOA can call the public `MetricOmmSimpleRouter` and execute swaps against the pool, draining LP-owned assets at oracle-derived prices. This is a direct loss of LP principal and a broken core pool invariant (the allowlist).

---

### Likelihood Explanation

Allowlisting the router is the **expected** operational step for any pool admin who wants their allowlisted users to access the protocol's standard swap interface. The admin has no reason to suspect this opens the gate to all users, because the extension's NatSpec says it "gates `swap` by swapper address" — implying per-user granularity. The bypass is therefore reachable through a routine, well-motivated admin action, not an exotic misconfiguration.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two sound approaches:

1. **Pass the originating user through the call chain.** Add an explicit `originator` parameter to `pool.swap()` (validated against `msg.sender` or a trusted router registry) and forward it as `sender` to extensions. The `SwapAllowlistExtension` then checks `allowedSwapper[pool][originator]`.

2. **Decode the originating user from `extensionData`.** The router encodes the originating EOA into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply honest data, which should be enforced by a router registry in the factory.

Either way, the extension must never treat an intermediate contract address as the economically relevant actor.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1)
  pool admin calls setAllowedToSwap(pool, alice, true)      // alice is allowlisted
  pool admin calls setAllowedToSwap(pool, router, true)     // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInput(...)
    → router calls pool.swap(recipient=bob, ...)
      → pool: msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes; bob receives token output
```

`bob` is never in the allowlist, yet the check passes because the router is allowlisted. The per-user gate is silently nullified. [3](#0-2) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
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
