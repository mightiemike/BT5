### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to make router-mediated swaps work on an allowlisted pool), every user — including explicitly non-allowlisted ones — can bypass the gate by routing through the router.

---

### Finding Description

`MetricOmmPool` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which is then forwarded verbatim to every configured extension:

```solidity
// MetricOmmPool.sol – simulateSwapAndRevert (same pattern as swap)
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, i.e. the router when routed
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value and dispatches it to each extension in order:

```solidity
// ExtensionCalling.sol
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
         packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput` (or any router entry point), the router calls `pool.swap(...)`. Inside the pool, `msg.sender` is the router contract. The pool therefore passes the **router address** as `sender` to `_beforeSwap`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| **No** | All router-mediated swaps revert — the router is unusable on this pool |
| **Yes** | Every user, including explicitly blocked ones, can bypass the allowlist by routing through the router |

There is no configuration that simultaneously enables the router and enforces per-user allowlisting.

---

### Impact Explanation

Any user blocked by the swap allowlist can trivially bypass it by calling `MetricOmmSimpleRouter` instead of the pool directly. The allowlist — the pool admin's primary mechanism for restricting who may trade — is rendered ineffective the moment the router is permitted. Pools deployed for regulatory compliance, KYC gating, or risk-controlled access lose their enforcement guarantee entirely. This is a broken core pool functionality / admin-boundary break with direct fund-impact potential (unauthorized swaps drain LP inventory at oracle prices).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public entry point for swaps. Any pool admin who configures a `SwapAllowlistExtension` and also wants users to be able to use the router will naturally allowlist the router, unknowingly opening the bypass. The trigger requires no privileged action beyond the pool admin's own expected setup steps.

---

### Recommendation

The pool must propagate the **originating user** through the hook call chain, not just `msg.sender`. Two viable approaches:

1. **Explicit sender parameter on `swap`**: Add a `sender` parameter to `IMetricOmmPoolActions.swap` so the router can pass `msg.sender` (the actual user) explicitly. The pool validates that `msg.sender == sender` for direct calls and trusts the router to supply the real user for routed calls (or the pool verifies the router is a trusted forwarder).

2. **Extension-data forwarding**: The router encodes the real user address in `extensionData`; `SwapAllowlistExtension` decodes and checks it when present, falling back to `sender` for direct calls. This requires a convention between the router and the extension.

Either way, the extension must gate the **economically relevant actor** (the end user), not the intermediary contract.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][router] = true   (to enable router use)
  pool admin: allowedSwapper[pool][userB]  = false  (userB is blocked)

Attack:
  userB calls MetricOmmSimpleRouter.exactInput({pool: pool, ...})
    → router calls pool.swap(recipient=userB, ...)
    → pool: msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap proceeds; userB's trade executes against LP inventory

Result:
  userB, explicitly blocked by the pool admin, successfully swaps.
  The SwapAllowlistExtension guard is fully bypassed.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
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
```
