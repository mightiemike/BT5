### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router address (the natural action to enable router-mediated swaps for their allowlisted users), the check passes for **every** caller of the router, regardless of whether that caller is on the per-user allowlist.

The pool admin faces an inescapable dilemma:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | **All** users bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks them for non-allowlisted users.

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` to enforce KYC, institutional-only, or compliance-gated access can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The bypassing user can execute swaps against the pool's LP reserves, extracting value (spread fees, favorable oracle prices) that the pool admin intended to restrict to vetted counterparties. This is a direct loss of LP-owed value and a broken core pool functionality (access control).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router must allowlist the router address. This is the expected operational path. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges — a single public call to `MetricOmmSimpleRouter.exactInput` or `exactOutput` suffices.

---

### Recommendation

The extension must verify the **original user**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a trusted router identity check inside the extension.

2. **Check `sender` only when the caller is not a trusted router**: The extension can maintain a registry of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

Either way, the allowlist must be keyed to the economically relevant actor (the user whose funds are being spent), not the intermediary contract.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `alice` as a swapper
  - Pool admin also allowlists `router` to enable router-mediated swaps for alice

Attack:
  - `bob` (not allowlisted) calls MetricOmmSimpleRouter.exactInput(pool, ...)
  - Router calls pool.swap() with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true (router is allowlisted)
  - Swap executes for bob, bypassing the per-user allowlist
```

The root cause is identical in structure to the external seed bug: a configured guard (`SwapAllowlistExtension`) validates one dimension (direct caller / `sender`) but the protocol's actual enforcement boundary requires a different dimension (original user), and no check bridges the two — exactly as the Cosmos-SDK checked total block size but not gas limits. [4](#0-3) [5](#0-4)

### Citations

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
