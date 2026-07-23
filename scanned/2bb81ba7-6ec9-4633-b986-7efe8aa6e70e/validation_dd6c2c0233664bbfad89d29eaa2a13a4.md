### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is always `msg.sender` of the `pool.swap()` call — against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, any unprivileged user can bypass the allowlist entirely by routing through the same public router.

---

### Finding Description

**Root cause — identity mismatch between the pool and the extension.**

`MetricOmmPool.swap` always forwards `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling` encodes that value verbatim and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the received `sender` against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router is `msg.sender` of the `pool.swap()` call. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

**The inescapable dilemma for the pool admin:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — they must call the pool directly |
| **Allowlist the router** | Every user, allowlisted or not, can bypass the gate by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

**Contrast with `DepositAllowlistExtension`**, which the research notes key on `owner` (the LP position owner), not `sender`. The swap extension has no equivalent "economically relevant actor" fallback — it only sees the immediate pool caller. [4](#0-3) 

---

### Impact Explanation

Any unprivileged user can swap in a pool that is supposed to be restricted to a specific set of counterparties by routing through the public `MetricOmmSimpleRouter`. The allowlist — the sole mechanism for restricting swap access — is rendered ineffective the moment the pool admin allowlists the router. Pools that use the allowlist to prevent adversarial or unvetted counterparties from trading against their LPs lose that protection entirely, exposing LP principal to the full universe of public traders.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router, which is a natural and expected operational step: without it, no allowlisted user can use the standard router UX. Once the router is allowlisted — a single `setAllowedToSwap(pool, router, true)` call — the bypass is unconditionally available to any address with no further preconditions.

---

### Recommendation

The extension must gate the **originating user**, not the immediate pool caller. Two viable approaches:

1. **`extensionData` forwarding**: The router encodes the original `msg.sender` into `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires a coordinated router + extension change but preserves the current interface.
2. **Separate `originalSender` field in the hook interface**: Add an `originalSender` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a transient slot set by the router before calling `pool.swap`.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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
