### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (the only way to permit router-mediated swaps for any user), every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

The `sender` parameter it receives is whatever the pool passes as the first argument to `_beforeSwap`. The pool always passes `msg.sender` — the direct caller of `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router becomes `msg.sender` of `pool.swap()`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

The allowlist is documented as gating "swapper address, per pool": [3](#0-2) 

But the actual check is on the intermediary (router), not the economically relevant actor (end user).

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC'd users). To allow those users to also use the router (the standard periphery entry point), the admin must allowlist the router address. Once the router is allowlisted, **any** address — including non-allowlisted users — can call `MetricOmmSimpleRouter` and have their swap accepted, because the extension only sees `sender = router`. The per-user allowlist is completely bypassed. This is a direct policy failure on curated pools: disallowed users can trade, draining LP assets at oracle-derived prices.

---

### Likelihood Explanation

The trigger is a standard public call to `MetricOmmSimpleRouter`. No privileged access, no malicious setup, and no non-standard tokens are required. Any user who knows the pool uses `SwapAllowlistExtension` can exploit this by routing through the router instead of calling the pool directly. The likelihood is high whenever a curated pool allowlists the router to support normal periphery usage.

---

### Recommendation

The extension must check the actual end user, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward the original `msg.sender` as part of `extensionData`, and `SwapAllowlistExtension` should decode and check it. Alternatively, the pool's `swap` interface could accept an explicit `originator` argument.

2. **Check `recipient` instead of `sender`** if the pool's design guarantees that `recipient` is always the end user — but this must be verified against the full call path.

The cleanest fix is for the router to pass the real user's address in `extensionData` and for the extension to decode and gate on that value, preserving the allowlist's intended semantics regardless of which periphery path is used.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker (non-allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Attacker's swap executes at oracle price, bypassing the curated allowlist entirely.

The invariant `"only allowlisted addresses may swap on this pool"` is broken for any pool that allowlists the router. [1](#0-0) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
