### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end-user. If the router is allowlisted for a pool, every user on the network can bypass the per-user swap allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the pool key and `sender` (the direct caller of `pool.swap`) as the identity to check: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(...)`. Inside the pool, `msg.sender` is the **router**, so `sender` passed to the extension is the **router address**. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

Two exploitable consequences follow:

1. **Bypass**: A pool admin allowlists the router (a natural step to enable router-based trading) → every unprivileged user can swap against the restricted pool by routing through `MetricOmmSimpleRouter`, regardless of whether they are individually allowlisted.

2. **Denial**: A pool admin allowlists specific end-users but does not allowlist the router → those users cannot swap through the router even though they are individually permitted, breaking the expected swap flow.

The bypass path is the fund-impacting case: the allowlist is the only on-chain gate protecting a restricted pool. Once the router is allowlisted, the gate is open to all.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter`. The attacker can execute arbitrary swaps against the restricted pool, draining liquidity at oracle-anchored prices without being an allowlisted party. This constitutes a direct loss of LP assets and a broken core pool invariant (the allowlist guard).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public entry point for swaps in the periphery layer.
- Pool admins who want to allow router-based trading for their allowlisted users must allowlist the router, which simultaneously opens the pool to all users.
- No special privilege or setup is required from the attacker beyond calling the public router.
- The misconfiguration is a natural, expected operational step, not an exotic edge case.

---

### Recommendation

The extension must resolve the original end-user identity, not the direct caller of `pool.swap`. Two approaches:

1. **Pass the original sender through the router**: `MetricOmmSimpleRouter` should accept an explicit `sender` parameter (or use `msg.sender` before the pool call) and forward it as `extensionData`. The `SwapAllowlistExtension` would then decode the original sender from `extensionData` and verify it matches `tx.origin` or a signed proof.

2. **Check `tx.origin` as a fallback**: When `sender` is a known router contract, fall back to `tx.origin`. This is simpler but introduces `tx.origin` risks.

3. **Separate router allowlisting from user allowlisting**: Document clearly that allowlisting the router opens the pool to all users, and provide a router wrapper that enforces its own per-user allowlist before calling the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInput(pool, ...)
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Check: allowedSwapper[pool][router] == true  → PASSES
  - Attacker's swap executes against the restricted pool

Result:
  - Attacker (not allowlisted) successfully swaps against a pool
    that was intended to be restricted to specific addresses only.
  - LP funds are exposed to an unauthorized counterparty.
``` [3](#0-2) [4](#0-3)

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
