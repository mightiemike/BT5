### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` at the pool level — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. If the pool admin allowlists the router (a plausible operational configuration), every user — including those not individually allowlisted — can bypass the per-user swap gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict swaps to specific, admin-approved addresses. Its `beforeSwap` hook checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. [2](#0-1) 

When `MetricOmmSimpleRouter` intermediates the swap, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`.

A pool admin who wants to allow router-mediated swaps for approved users would naturally allowlist the router address. But doing so opens the gate to **every** user who calls the router, because the extension has no visibility into who called the router. The allowlist invariant — "only approved addresses may swap" — is silently broken for all router-mediated paths.

The `simulateSwapAndRevert` path also calls `_beforeSwap` with `msg.sender` as `sender`, confirming the binding is structural, not incidental. [3](#0-2) 

The `SwapAllowlistExtension` interface signature confirms `sender` is the only identity checked — `recipient` (the actual output receiver) is silently discarded (`address,`): [4](#0-3) 

---

### Impact Explanation

Any user can bypass a configured swap allowlist by routing through `MetricOmmSimpleRouter`. Pools intended to be restricted to specific counterparties (e.g., institutional market makers, KYC'd participants) become effectively open to the public for swaps. This breaks the admin-configured access boundary and can cause direct LP losses if the pool's pricing assumptions depend on trading only with trusted, non-adversarial counterparties.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to allowlist the router address. This is a plausible, non-malicious configuration: an admin who wants to allow router-mediated swaps for approved users would allowlist the router, not realizing that this opens the pool to all router users. `MetricOmmSimpleRouter` is a public, permissionless contract, so once the router is allowlisted, any user can exploit the bypass without further admin involvement.

The research guidance explicitly flags this path: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

---

### Recommendation

The `SwapAllowlistExtension` should gate on the actual end-user's identity, not the direct caller of `pool.swap()`. Options:

1. The router should forward the original caller's address in `extensionData`, and the extension should decode and check that address instead of (or in addition to) `sender`.
2. Alternatively, document clearly that allowlisting the router is semantically equivalent to `allowAllSwappers = true`, so admins understand the implication before making that configuration choice.
3. Consider a two-level check: if `sender` is a known periphery router, fall back to checking `recipient` or a decoded address from `extensionData`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in the `BEFORE_SWAP_ORDER` slot.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allowlist `MetricOmmSimpleRouter`, intending to allow router-mediated swaps for approved users.
3. Non-allowlisted user `alice` calls `router.exactInput(...)` targeting the pool.
4. Router calls `pool.swap(alice, ...)` — at the pool level, `msg.sender = router`.
5. Pool calls `extension.beforeSwap(router, alice, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
6. Swap executes successfully for `alice`, who was never individually allowlisted.

**Corrupted invariant**: `allowedSwapper[pool][alice]` is `false`, yet `alice` successfully swaps. The accounting variable that should gate her — her individual allowlist entry — is never consulted, exactly as `totalTokenRewardsPerStake` was never updated in the `migrateToken` analog. [6](#0-5) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-42)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L319-331)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
