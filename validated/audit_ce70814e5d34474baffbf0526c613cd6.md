### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. However, the pool passes `msg.sender` of its own `swap` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the router is allowlisted (the only way router-mediated swaps can work at all), every user in the world can bypass the swap allowlist by routing through the router. The check exists and looks correct, but it gates the wrong actor — a direct structural analog to the HoneyJarPortal `_from != _msgSender()` redundant check.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct pool identity), and `sender` is the value the pool forwarded. The pool's `ExtensionCalling._beforeSwap` is called with `msg.sender` of the pool's own `swap` function:

```
pool.swap(recipient, zeroForOne, ...) called by router
  → _beforeSwap(msg.sender /*= router*/, recipient, ...)
    → extension.beforeSwap(router, ...)
      → allowedSwapper[pool][router]  ← checked, NOT the real user
```

The pool's `addLiquidity` NatSpec explicitly documents the operator pattern: *"msg.sender pays but need not equal owner (operator pattern)."* The same pattern applies to `swap` — the router is the `msg.sender` to the pool, not the end user.

**Two broken scenarios:**

1. **Allowlist bypass (critical path):** Pool admin allowlists specific KYC'd addresses and also allowlists the router (required for router-mediated swaps to function). Because the extension checks `allowedSwapper[pool][router]`, every user who calls `MetricOmmSimpleRouter.exactInputSingle(...)` passes the check regardless of whether they are individually allowlisted. The curated pool is fully open to any user via the router.

2. **Allowlisted users locked out:** If the admin does NOT allowlist the router, individually allowlisted users cannot swap through the router even though they are on the allowlist. Core pool functionality is broken for the intended users.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (institutional, KYC, or whitelist-only pools) is completely bypassed by any user routing through the public `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool's liquidity, draining LP value at oracle-derived prices. This is a direct loss of LP principal attributable to a broken access-control invariant on a core pool action.

---

### Likelihood Explanation

**High.** No special permissions or preconditions are required. Any user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the curated pool. The router is a public, permissionless contract. The only precondition is that the pool admin has allowlisted the router (which is the natural and necessary configuration for router-based swaps to work at all).

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary router. Two approaches:

1. **Pass the original caller through the router:** Have the router forward the original `msg.sender` as a separate field in `extensionData`, and have the extension decode and check that address. This requires a coordinated change between the router and extension.

2. **Check `sender` only when it is the direct caller:** The extension should revert if `sender` is a known router/intermediary and the actual user identity is not verifiable. Alternatively, the pool could expose a `swapOnBehalf(address realUser, ...)` entry point that passes `realUser` as `sender` to extensions.

The simplest correct fix is to require that the router always passes the originating user address in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router swaps
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // intended allowlist
  - Pool admin does NOT call setAllowedToSwap(pool, eve, true)

Attack:
  - eve (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=eve, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Swap executes for eve despite her not being on the allowlist

Result:
  - eve successfully swaps on a curated pool she was never authorized to access
  - LP funds are exposed to any public user via the router
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-148)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
