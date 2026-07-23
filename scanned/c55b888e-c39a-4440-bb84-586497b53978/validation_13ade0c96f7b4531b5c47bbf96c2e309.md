### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the end-user's address. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently grants every unprivileged user the ability to bypass the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool populates with its own `msg.sender` — i.e., whoever called `pool.swap()`. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` or `exactOutputSingle()`, the router is the entity that calls `pool.swap()`. Therefore `sender` = router address, and the check becomes:

```
allowedSwapper[pool][router]
```

The extension has no visibility into who called the router. The pool's `swap()` interface accepts `recipient` (output destination) and `callbackData` (settlement bytes), but no field carries the original end-user identity to the extension layer. [3](#0-2) 

This creates a binary, irreconcilable choice for the pool admin:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user on the network can swap; per-user allowlist is nullified |
| Do not allowlist the router | No user can ever swap through the router on this pool |

There is no configuration that allows "only KYC'd users via the router."

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting swap access to specific counterparties (e.g., KYC'd traders, institutional desks). Bypassing it allows arbitrary unprivileged users to execute swaps against a pool whose LP positions were sized and priced under the assumption of a trusted, restricted counterparty set. Toxic or adversarial flow from non-allowlisted users can drain LP value through adverse selection, directly reducing the token balances owed to LPs on removal. This is a broken core pool functionality / LP asset loss path above Sherlock medium thresholds.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entry point. Any pool admin who wants allowlisted users to interact via the standard UI/router must allowlist the router address. This is the expected operational path, making the precondition realistic and the bypass trivially reachable by any unprivileged caller. [4](#0-3) 

---

### Recommendation

The extension must gate the original end-user, not the intermediary. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a trusted router convention and the extension to verify `msg.sender` (the pool) is a known pool before trusting the decoded identity.

2. **Pool-level `tx.origin` or explicit caller field**: Add an explicit `originalCaller` field to the swap interface that the pool populates with `tx.origin` or a signed identity, and forward it through the extension hook. (Note: `tx.origin` has its own trust assumptions.)

The simplest safe fix is approach (1): the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must do this for any router swap
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  1. alice (non-allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, callbackData, extensionData)
       → msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  ✓
  5. Swap executes. Alice swaps successfully despite not being on the allowlist.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [1](#0-0) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```
