### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address — not the actual user. If the router is allowlisted (which is required for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

That `sender` value is forwarded verbatim to every configured extension. `SwapAllowlistExtension.beforeSwap()` then checks it against the allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` entry point), the router calls `pool.swap()` directly. At that point:

- `msg.sender` inside the pool = **router address**
- `sender` delivered to `beforeSwap` = **router address**
- The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`

The pool admin faces an inescapable dilemma:

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Every user on the network bypasses the per-user allowlist by routing through the public router |
| **No** | Allowlisted users cannot use the router at all; the router is silently broken for this pool |

The allowlist is designed to gate specific users. In neither case does it do so correctly for router-mediated swaps.

The `allowedSwapper` and `allowAllSwappers` mappings are keyed by pool and swapper: [3](#0-2) 

The admin setter operates on the same key space: [4](#0-3) 

There is no mechanism for the extension to recover the original caller's address from the router — the router's identity fully replaces the user's identity by the time the hook fires.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. The non-allowlisted user receives real token output from the pool; the pool's LP providers bear the counterparty risk of trades they were explicitly configured to reject. This is a direct loss of the curation guarantee and a fund-impacting policy bypass.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role, privilege, or setup is required to call it. Any user who knows the pool address can route through it. The only precondition is that the pool admin has allowlisted the router (which they must do if they want any router-mediated swaps to work at all). This is a normal operational step, making the bypass reachable in any realistic deployment of an allowlisted pool that also supports the periphery router.

---

### Recommendation

The extension must check the identity of the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to be excluded from allowlisted pools**: Document that allowlisted pools must not be used with the public router, and enforce this at pool creation or via a separate router that forwards the original caller.

The cleanest fix is approach (1): the router always appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap()` decodes the real caller from `extensionData` when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin calls setAllowedToSwap(pool, userA, true)   // only userA is allowed
  admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for router swaps

Attack:
  userB (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls:
    pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)
    // msg.sender inside pool = router

  Pool calls _beforeSwap(sender=router, ...)

  Extension checks:
    allowedSwapper[pool][router] == true  ✓  (admin set this)
    → hook passes, swap executes

  Result:
    userB receives token output from the pool.
    The allowlist that was supposed to block userB is silently bypassed.
    LP providers bear the trade they were configured to reject.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
