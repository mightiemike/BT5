### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension receives the router address as `sender` and checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router (required for router-mediated swaps to work at all), every unprivileged user can bypass the curated allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is called by the pool via `ExtensionCalling._beforeSwap`, which forwards the `sender` argument it received from the pool's `swap` function. The pool's `swap` function passes `msg.sender` as `sender` — the direct caller of `swap`. When a user calls `MetricOmmSimpleRouter.exactInput*`, the router is `msg.sender` to the pool, so the extension receives `sender = router`.

The check inside the extension is:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is the router (wrong — should be the actual user). The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → router-mediated swaps always revert; the pool is unusable via the standard periphery.
- **Allowlist the router** → every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through the public router.

The call chain is:

```
User (not allowlisted)
  → MetricOmmSimpleRouter.exactInput*(...)
    → MetricOmmPool.swap(recipient, zeroForOne, amount, priceLimit, extensionData)
      [pool msg.sender = Router]
      → ExtensionCalling._beforeSwap(sender=Router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=Router, ...)
          → allowedSwapper[pool][Router] == true  ← bypass
```

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, whitelisted market makers, or compliance-gated participants) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP funds, violating the pool's intended access policy. This constitutes an admin-boundary break via an unprivileged public path and can cause direct loss of LP principal if the restricted pool was designed to trade only with trusted counterparties at favorable terms.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who discovers the bypass can exploit it immediately with no special privileges. The pool admin must allowlist the router for the pool to be usable via the standard interface, making the bypass condition the expected production configuration.

---

### Recommendation

The pool should pass the original user's address as `sender` to the extension, not `msg.sender`. Two approaches:

1. **Router forwards the real user**: `MetricOmmSimpleRouter` passes `msg.sender` (the actual user) as an explicit `sender` parameter to `pool.swap`, and the pool forwards it to extensions instead of using `msg.sender`. This requires a pool-level API change.

2. **Extension checks `recipient` or a signed identity**: If the pool API cannot change, the extension should check the `recipient` field (which the router sets to the actual user) or require a signed proof of identity in `extensionData`.

The simplest fix consistent with the existing architecture is option 1: add a `sender` parameter to `IMetricOmmPool.swap` so the router can pass `msg.sender` through, and the pool forwards it to `_beforeSwap` instead of its own `msg.sender`.

---

### Proof of Concept

```solidity
// Pool admin sets up a restricted pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(address(factory));
// allowedUser is KYC'd; attacker is not
ext.setAllowedToSwap(address(pool), allowedUser, true);
// Admin must also allowlist the router for the pool to be usable
ext.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the gate via the router
vm.prank(attacker);
// Direct call would revert: pool.swap(...) → NotAllowedToSwap
// Router call succeeds because extension sees sender=router (allowlisted)
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: true,
        amountIn: 1000e6,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Attacker successfully swapped on a pool they were explicitly excluded from
```

---

**Relevant code locations:**

`SwapAllowlistExtension.beforeSwap` — the check that gates on `sender` (the router, not the user): [1](#0-0) 

`ExtensionCalling._beforeSwap` — forwards `sender` from the pool's swap call to the extension: [2](#0-1) 

`SwapAllowlistExtension` allowlist storage — keyed by `(pool, swapper)` where swapper becomes the router: [3](#0-2)

### Citations

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
