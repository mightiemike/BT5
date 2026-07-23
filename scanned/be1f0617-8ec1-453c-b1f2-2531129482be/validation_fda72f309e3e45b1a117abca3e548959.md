Looking at the key files, I need to trace the exact identity checked by `SwapAllowlistExtension.beforeSwap` when a user routes through `MetricOmmSimpleRouter` versus calling the pool directly.

**Trace:**

1. `pool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` → extension
2. Direct call: `sender` = actual user ✓
3. Router call: `sender` = router address ✗ (router is `msg.sender` of `pool.swap()`)
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` — when routed, this is `allowedSwapper[pool][router]`

If the admin allowlists the router to enable router-mediated swaps for allowlisted users, every router user bypasses the per-user gate.

---

### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension` is designed to gate swaps by individual swapper identity. However, `beforeSwap` checks `sender`, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address. If the pool admin allowlists the router (a natural action to enable router-mediated swaps), every user of the router bypasses the per-user allowlist entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to the extension dispatcher: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls the pool directly, `sender` = user address — the check is correct. When a user calls through `MetricOmmSimpleRouter`, `sender` = router address — the check is against the router, not the user.

The allowlist admin setter stores entries keyed by `[pool][swapper]`: [4](#0-3) 

A pool admin who wants to allow router-mediated swaps for their allowlisted users will naturally call `setAllowedToSwap(pool, router, true)`. This single entry makes `allowedSwapper[pool][router] = true`, so every user of the router — including addresses that were never individually allowlisted — passes the gate. The extension ignores the actual end-user identity entirely when the swap is router-mediated, directly analogous to the external bug where the version argument is ignored and the wrong value is used.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for compliance or access-control purposes (e.g., KYC-gated liquidity, institutional-only pools) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes swaps against pool liquidity that was intended to be restricted, receiving output tokens from the pool. This is a direct loss-of-access-control impact: the pool's LP assets are exposed to counterparties the pool admin explicitly excluded.

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected administrative action — without it, no user can use the router against the pool at all. An admin who wants to support both direct and router-mediated swaps for their allowlisted users will allowlist the router, not realizing this opens the gate to all router users. The trigger after that point is fully unprivileged: any address calls the router.

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the direct pool caller. Two options:

1. **Pass the real user via `extensionData`**: The router encodes the originating user address into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct address.
2. **Document the limitation and prohibit router allowlisting**: Add an explicit NatSpec warning that allowlisting any intermediary contract (router, multicall) grants access to all users of that contract, and that per-user gating only works for direct pool callers.

Option 1 is the only approach that preserves the intended per-user semantics for router-mediated swaps.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  admin: setAllowedToSwap(pool, router, true)       // enable router-mediated swaps

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInput(...)
    → router calls pool.swap(recipient=bob, ...)
    → pool: _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓
    → swap proceeds, bob receives output tokens

Direct call check (bob, not allowlisted):
  bob calls pool.swap(...) directly
    → SwapAllowlistExtension.beforeSwap(sender=bob, ...)
    → allowedSwapper[pool][bob] == false  → revert NotAllowedToSwap ✓

Result: bob bypasses the allowlist via the router.
``` [3](#0-2) [5](#0-4) [2](#0-1)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
