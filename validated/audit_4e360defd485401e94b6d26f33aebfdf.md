### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension sees the **router address** as the swapper identity — not the actual end-user. Any non-allowlisted user can therefore bypass the curated-pool gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist, keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInput/exactOutput`, the router calls `pool.swap()` on the user's behalf. At that point:

- `pool.swap()` sees `msg.sender = router`
- `sender` forwarded to the extension = **router address**
- The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`

If the pool admin allowlists the router (the only way to enable router-mediated swaps on a curated pool), every user — including those explicitly excluded from the allowlist — can swap freely by routing through the router. The per-user gate is completely bypassed.

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional partners). The allowlist is the sole access-control mechanism for swaps. Once the router is allowlisted to support normal UX, the allowlist provides zero protection: any address can call `MetricOmmSimpleRouter` and execute swaps against the pool. Unauthorized users can drain LP liquidity at oracle prices, extract spread fees, or trade against positions that were never meant to be exposed to them. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed alongside the protocol. Any pool that (a) deploys `SwapAllowlistExtension` and (b) wants to support router-mediated swaps — the normal operational mode — must allowlist the router, triggering the bypass. The attacker needs no special privilege: a single public call to the router suffices. The condition is reachable on every production curated pool that uses the router.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: `MetricOmmSimpleRouter` should pass the caller's address as a verified `sender` field in `extensionData`, and the extension should decode and check that field when `msg.sender` (the pool's caller) is a known router.

2. **Alternatively, check `tx.origin` as a fallback** (only acceptable if the threat model excludes contract callers) or require the pool's `swap()` to accept an explicit `swapper` parameter that the router populates with `msg.sender` before calling the pool.

The cleanest fix matching the deposit-side design (which correctly checks `owner`, the position beneficiary, not `sender`, the payer) is to add a verified `swapper` field to the swap call path so the extension always sees the true initiating address regardless of routing intermediary.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured. Only `alice` is allowlisted: `allowedSwapper[pool][alice] = true`.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for `alice`.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. `_beforeSwap(router, ...)` is dispatched; extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `bob` successfully swaps on a pool he was explicitly excluded from, receiving tokens at oracle price.

The allowlist invariant is broken: `bob` receives output tokens from a curated pool without authorization, constituting a direct loss of LP assets to an unauthorized counterparty. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
