### Title
Swap Allowlist Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to support router-mediated swaps on a curated pool), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When a user calls the pool directly, `sender == user` and the check is correct. When a user calls through `MetricOmmSimpleRouter`, `sender == router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool that uses `SwapAllowlistExtension` and also supports the router:

- **Router not allowlisted:** Allowlisted users cannot swap through the router at all — broken core functionality.
- **Router allowlisted:** Every user, including those explicitly excluded from the allowlist, can swap through the router and bypass the gate entirely.

The pool admin has no way to simultaneously support router-mediated swaps and enforce a per-user allowlist. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, protocol-owned addresses, or whitelisted market makers) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps on a pool that was designed to reject them, draining liquidity or extracting value from a pool whose LP depositors expected access control to be enforced. This is a direct loss of the allowlist protection and constitutes broken core pool functionality with fund-impacting consequences. [5](#0-4) 

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public periphery entrypoint documented and supported by the protocol. Any user aware of the router can trivially route through it. The pool admin has no on-chain mechanism to prevent this without also blocking all allowlisted users from using the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. [3](#0-2) 

---

### Recommendation

The extension must gate on the **original user**, not the intermediate router. Two approaches:

1. **Pass the original caller through the router:** `MetricOmmSimpleRouter` should accept an explicit `swapper` parameter (or use `msg.sender` before calling the pool) and pass it as `extensionData` or as the `recipient` field so the extension can recover it. The extension then reads the true user from `extensionData` rather than from `sender`.

2. **Check `recipient` instead of `sender` for router flows:** Since the router typically sets `recipient` to the actual user, the extension could be redesigned to gate on `recipient` when `sender` is a known router — but this requires a registry of trusted routers and is fragile.

The cleanest fix is for the pool's `swap` interface to carry a separate `originator` field that the router populates with `msg.sender` before forwarding to the pool, and for `SwapAllowlistExtension` to check that field. [3](#0-2) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = [1].
  - Pool admin allowlists userA: allowedSwapper[pool][userA] = true.
  - Pool admin also allowlists the router: allowedSwapper[pool][router] = true
    (required so that userA can use the router).

Attack:
  1. userB (not allowlisted) calls MetricOmmSimpleRouter.exactInput(..., pool, ...).
  2. Router calls pool.swap(recipient=userB, ...) — pool's msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes for userB despite userB never being allowlisted.

Result:
  userB successfully swaps on a pool that was designed to exclude them.
  The allowlist is nullified for all users as long as the router is allowlisted.
``` [5](#0-4) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
}
```
