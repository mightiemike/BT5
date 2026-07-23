### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` always passes its own `msg.sender` as `sender`, routing through the public `MetricOmmSimpleRouter` causes the extension to check the router's address rather than the actual end-user's address. Any pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the pool to every user on the network.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by the pool. [1](#0-0) 

Inside `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, recipient, ...)`, so `sender` is always the **immediate caller of the pool's `swap` function**. [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
user → MetricOmmSimpleRouter.exact*() → pool.swap()
```

The pool's `msg.sender` is the **router address**, not the user. The extension therefore evaluates:

```
allowedSwapper[pool][router_address]
```

not

```
allowedSwapper[pool][actual_user]
```

A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router address. Once the router is allowlisted, **every user on the network** can call `MetricOmmSimpleRouter` and pass the extension check, regardless of whether they are individually allowlisted. The per-user allowlist is completely bypassed. [3](#0-2) 

The `extensionData` forwarding path does not help here: the router forwards raw bytes to the pool, but `SwapAllowlistExtension` ignores `extensionData` entirely and relies solely on the `sender` argument. [1](#0-0) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted counterparties loses its access control entirely once the router is allowlisted. Any unprivileged user can trade against the pool's liquidity, exposing LPs to counterparties they explicitly excluded. This constitutes a broken core pool functionality and a direct admin-boundary break: an unprivileged path (the public router) bypasses a configured pool-level guard. The impact is **High**.

---

### Likelihood Explanation

The likelihood is **Medium-High**. Pool admins who deploy a swap-allowlisted pool and also want to support the standard periphery router (a natural and documented use case) will allowlist the router. The `MetricOmmSimpleRouter` is a public, permissionless contract. No special privileges, flash loans, or multi-step setup are required — any user simply calls the router's `exactInput` or equivalent function.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the immediate pool caller. Two sound approaches:

1. **Router forwards original sender**: `MetricOmmSimpleRouter` should pass the original `msg.sender` as part of `extensionData`, and `SwapAllowlistExtension` should decode and check that address when `sender` is a known router.

2. **Pool exposes original sender**: Add an optional `originalSender` field to the swap call that the pool passes through to extensions, allowing the extension to check the true initiator regardless of routing depth.

Either way, the invariant must be: the allowlist check keys on the address that economically initiates and benefits from the swap, not the contract that mechanically forwards it.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured (allowAllSwappers = false)
  - Pool admin calls setAllowedToSwap(pool, router_address, true)
    (to enable router-mediated swaps for allowlisted users)
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInput(..., pool, ...)
  2. Router calls pool.swap(recipient=bob, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender=router_address
  5. Check: allowedSwapper[pool][router_address] == true → PASSES
  6. bob's swap executes successfully despite not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowlist fully bypassed
``` [4](#0-3) [2](#0-1)

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
