### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address as `sender` instead of the actual end-user, allowing any unprivileged user to bypass the configured swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate which addresses may swap against a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool always sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks the router's address rather than the actual end-user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every user — including those explicitly excluded from the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` always passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[pool][sender]`, where `msg.sender` inside the extension is the pool and `sender` is the value forwarded from the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(recipient, ...)`. The pool's `msg.sender` is the router, so `sender` passed to the extension is the router's address — not the end-user's address. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes:

1. **Router not allowlisted**: Allowlisted users cannot swap through the router (broken core swap flow).
2. **Router allowlisted** (the natural fix): Every user — including those explicitly blocked — can bypass the allowlist by routing through the router.

The pool admin has no way to simultaneously allow router-mediated swaps and enforce per-user allowlist restrictions, because the extension has no mechanism to unwrap the router and inspect the originating user.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., to prevent toxic arbitrage flow, enforce KYC compliance, or gate a launch period) loses that protection entirely once the router is allowlisted. Any unprivileged user can route through `MetricOmmSimpleRouter` and execute swaps against pool liquidity. LPs are exposed to the exact counterparty flow the allowlist was meant to block, leading to direct LP principal loss through adverse selection or unauthorized access to pool reserves.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. A pool admin who configures a swap allowlist will almost certainly also need to allowlist the router to support normal UX. The bypass is then immediately available to any user with no special privileges, no malicious setup, and no non-standard token behavior required.

---

### Recommendation

The `beforeSwap` hook should gate on the actual end-user identity, not the direct pool caller. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Check `recipient` instead of `sender`**: For swap allowlists, gating on `recipient` (the address receiving output tokens) may better reflect the intended economic actor, though it shifts the semantic.
3. **Document that the allowlist gates the direct pool caller only**, and require pools that need per-user gating to prohibit router access entirely (i.e., do not allowlist the router).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, userA, true)   // only userA allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX

Attack:
  - userB (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
  - Router calls pool.swap(userB_as_recipient, ...)
  - Pool calls extension.beforeSwap(router, userB, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for userB despite userB not being in the allowlist

Result:
  - userB bypasses the configured swap allowlist
  - LPs are exposed to swaps from non-allowlisted counterparties
  - The allowlist guard is silently rendered ineffective
``` [3](#0-2) [4](#0-3) [2](#0-1)

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
