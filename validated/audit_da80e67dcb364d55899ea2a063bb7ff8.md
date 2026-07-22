### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate which addresses may swap on a curated pool. Its `beforeSwap` hook checks the `sender` argument, which is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the user's address. If the pool admin allowlists the router (a necessary step to permit any router-mediated swap), every unpermissioned user can bypass the allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes `sender` (the pool's `msg.sender`) into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is in the per-pool allowlist, using `msg.sender` (the pool) as the namespace key: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap(...)` directly. At that point, `msg.sender` inside the pool is the **router**, not the user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable mismatch:

| Entry path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| User → `pool.swap()` directly | user address | `allowedSwapper[pool][user]` |
| User → `router.exact*()` → `pool.swap()` | router address | `allowedSwapper[pool][router]` |

A pool admin who wants to permit router-based swaps must allowlist the router. Once the router is allowlisted, **every** user — including those explicitly excluded from the allowlist — can bypass the gate by calling the public router instead of the pool directly.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position recipient), which is passed explicitly through every call path and is not substituted by an intermediary address. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The bypassing user receives the full output of the swap at oracle-anchored prices, constituting a direct policy breach with fund-impacting consequences: the pool's LP providers are exposed to trades from actors the pool admin explicitly intended to exclude, and any fee or risk model predicated on a closed participant set is violated.

---

### Likelihood Explanation

The likelihood is high. `MetricOmmSimpleRouter` is the standard, publicly documented periphery entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-based swaps for their allowlisted users must allowlist the router — at which point the bypass is immediately available to all users. The router is a permissionless public contract; no privileged access or special setup is required by the attacker beyond knowing the router address.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary fixes:

1. **Router-level**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when present, falling back to `sender` for direct calls.

2. **Extension-level**: Document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps unless the router itself is the intended gating boundary, and add a NatDoc warning to `beforeSwap` stating that `sender` is the direct pool caller, not the end user.

A minimal diff for the extension:

```solidity
// In SwapAllowlistExtension.beforeSwap:
// Decode real user from extensionData if present (router path),
// otherwise fall back to sender (direct path).
address effectiveSender = extensionData.length >= 20
    ? abi.decode(extensionData, (address))
    : sender;
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only allowed swapper
  allowedSwapper[P][router] = true  // admin allowlists router so alice can use it

Attack:
  bob (not in allowlist) calls:
    router.exactInput(pool=P, recipient=bob, ...)

Execution:
  router calls pool.swap(recipient=bob, ...)
    → msg.sender inside pool = router
    → _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[P][router] → true → passes
  swap executes, bob receives output tokens

Result:
  bob, an explicitly excluded address, completes a swap on a curated pool.
  The allowlist invariant is broken; any user can bypass it via the public router.
```

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
