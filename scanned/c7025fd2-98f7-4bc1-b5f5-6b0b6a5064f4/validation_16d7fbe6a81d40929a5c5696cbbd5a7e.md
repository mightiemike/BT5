### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `MetricOmmPool.swap` — the router contract — not the end user. When a pool admin allowlists the router to enable router-mediated swaps, every unpermissioned user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The wrong actor is bound to the guard parameter, exactly mirroring the PheasantNetwork class of bug (wrong parameter used in a check function).

---

### Finding Description

**Root cause — wrong identity bound to the guard parameter.**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `ExtensionCalling._beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called the pool: [3](#0-2) 

When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. The allowlist therefore checks `allowedSwapper[pool][router]` — the router's address — rather than the actual end user's address.

The allowlist admin API is keyed by `(pool, swapper)` address pairs: [4](#0-3) 

There is no mechanism to recover the original user's address inside the extension; the extension only receives the `sender` parameter the pool supplies.

---

### Impact Explanation

Two fund-impacting outcomes arise from the wrong-actor binding:

1. **Allowlist bypass (High).** If the pool admin allowlists the router address (the natural step to enable router-mediated swaps for any permitted user), the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including completely unpermissioned users. The curated pool's access control is fully defeated. Unpermissioned users can execute swaps, draining liquidity or extracting value from a pool that was designed to be restricted.

2. **Broken core swap path for permitted users (Medium/High).** If the pool admin does **not** allowlist the router (to avoid the bypass above), then every allowlisted user who attempts to swap through `MetricOmmSimpleRouter` is rejected. The router — the primary supported periphery entry point — is unusable for the pool, breaking the core swap flow for legitimate participants.

Both outcomes are direct consequences of the wrong parameter (`sender` = router) being supplied to the guard.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entry point described in the protocol documentation and is expected to be used by the vast majority of users.
- A pool admin who deploys a `SwapAllowlistExtension` and wants to allow router-mediated swaps for permitted users will naturally allowlist the router, triggering outcome 1.
- No privileged action by the attacker is required: any user can call `MetricOmmSimpleRouter.exactInputSingle` (or equivalent multi-hop path) targeting the restricted pool.
- The bypass is reachable on every swap through the router with zero preconditions beyond the pool admin having allowlisted the router.

---

### Recommendation

The extension must check the **end user's identity**, not the intermediary's. Two standard approaches:

1. **Pass the original user through the router.** Have `MetricOmmSimpleRouter` pass the user's address as the `sender` argument when calling `pool.swap`, so the pool forwards the real user to the extension. This requires the pool's `swap` interface to accept an explicit `sender` parameter rather than using `msg.sender`.

2. **Check `recipient` instead of `sender` for router flows.** If the pool's design guarantees that `recipient` is always the end user (even through the router), the extension can gate on `recipient`. However, this must be verified against the full call path.

The cleanest fix is approach 1: the router should forward the originating user's address as `sender` so the allowlist always gates the economically relevant actor, regardless of which supported periphery path is used.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured in BEFORE_SWAP_ORDER.
2. Pool admin calls E.setAllowedToSwap(P, router, true)
   — intending to allow router-mediated swaps for permitted users.
3. Attacker (address A, NOT in the allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
4. Router calls P.swap(/* msg.sender = router */, ...)
5. Pool calls _beforeSwap(sender=router, ...)
6. E.beforeSwap receives sender=router.
7. Check: allowedSwapper[P][router] == true  ✓  → passes.
8. Attacker's swap executes on the restricted pool.
   allowedSwapper[P][A] was never set — the guard was never consulted for A.
```

The guard checked the wrong parameter (`sender` = router) instead of the actual end user (`A`), directly mirroring the PheasantNetwork class of bug where the wrong address was supplied to a check function. [3](#0-2) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-19)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
